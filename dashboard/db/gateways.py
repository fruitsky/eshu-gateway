import time
from db.core import db_conn

def init_gateways_tables(cursor):
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS gateways (
            ip TEXT PRIMARY KEY,
            hostname TEXT NOT NULL,
            last_seen INTEGER NOT NULL,
            version TEXT DEFAULT 'v6.0',
            policy_version INTEGER DEFAULT 0
        )
    ''')
    for col, default in [('version', "'v6.0'"), ('policy_version', '0'), ('last_policy_sync', '0'), ('first_seen', 'NULL'), ('last_updated', '0'), ('api_token', 'NULL')]:
        try:
            cursor.execute(f"ALTER TABLE gateways ADD COLUMN {col} TEXT DEFAULT {default}")
        except Exception:
            pass
    cursor.execute("UPDATE gateways SET api_token = NULL WHERE api_token = 'None'")
    try:
        cursor.execute("ALTER TABLE gateways ADD COLUMN windows_count INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE gateways ADD COLUMN last_heartbeat INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE gateways ADD COLUMN heartbeat_poller_ok INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE gateways ADD COLUMN heartbeat_gateway_ok INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE gateways ADD COLUMN heartbeat_can_reach INTEGER DEFAULT 0")
    except Exception:
        pass
    # v15+ migration: add mode column, token column
    for col, defval in [('mode', "'prod'")]:
        try:
            cursor.execute(f"ALTER TABLE gateways ADD COLUMN {col} TEXT DEFAULT {defval}")
        except Exception:
            pass
    try:
        cursor.execute("ALTER TABLE gateways ADD COLUMN zero_trust INTEGER DEFAULT 0")
    except Exception:
        pass
    # Override mode columns
    try:
        cursor.execute("ALTER TABLE gateways ADD COLUMN override_until INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE gateways ADD COLUMN override_reason TEXT DEFAULT ''")
    except Exception:
        pass

def register_gateway(ip: str, hostname: str, version: str = "v6.0"):
    with db_conn() as conn:
        cursor = conn.cursor()
        now = int(time.time())
        cursor.execute('UPDATE gateways SET hostname = ?, last_seen = ?, version = ? WHERE ip = ?',
                       (hostname, now, version, ip))
        if cursor.rowcount == 0:
            # Insert with api_token explicitly NULL — never rely on the column
            # default, which can be the literal string 'None' (v15.0-era schema)
            # and would otherwise be returned as a fake token by /api/register.
            cursor.execute('''
                INSERT INTO gateways (ip, hostname, last_seen, first_seen, version, api_token)
                VALUES (?, ?, ?, ?, ?, NULL)
            ''', (ip, hostname, now, now, version))
        conn.commit()

def get_gateways():
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM gateways ORDER BY ip ASC')
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

def update_gateway_last_seen(ip: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        now = int(time.time())
        cursor.execute('UPDATE gateways SET last_seen = ? WHERE ip = ?', (now, ip))
        conn.commit()

def update_gateway_policy_version(ip: str, version: int):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE gateways SET policy_version = ? WHERE ip = ?', (version, ip))
        conn.commit()

def update_gateway_policy_sync(ip: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        now = int(time.time())
        cursor.execute('UPDATE gateways SET last_policy_sync = ? WHERE ip = ?', (now, ip))
        conn.commit()

def update_gateway_last_updated(ip: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        now = int(time.time())
        cursor.execute('UPDATE gateways SET last_updated = ? WHERE ip = ?', (now, ip))
        conn.commit()

def deregister_gateway(ip: str) -> bool:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM gateways WHERE ip = ?', (ip,))
        deleted = cursor.rowcount > 0
        conn.commit()
        return deleted

def get_gateway_token(ip: str) -> str:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT api_token FROM gateways WHERE ip = ?', (ip,))
        row = cursor.fetchone()
        return row['api_token'] if row else ''

def set_gateway_token(ip: str, token: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE gateways SET api_token = ? WHERE ip = ?', (token, ip))
        conn.commit()

def get_gateway_by_token(token: str) -> dict:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM gateways WHERE api_token = ?', (token,))
        row = cursor.fetchone()
        return dict(row) if row else None

def set_trigger_uninstall(ip: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        now = int(time.time())
        cursor.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('trigger_uninstall_' || ?, ?)", (ip, str(now)))
        conn.commit()

def check_trigger_uninstall(ip: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM meta WHERE key = ?", (f"trigger_uninstall_{ip}",))
        row = cursor.fetchone()
        return row['value'] if row else None

def clear_trigger_uninstall(ip: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM meta WHERE key = ?", (f"trigger_uninstall_{ip}",))
        conn.commit()

def set_uninstall_progress(ip: str, step: str, message: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('uninstall_progress_' || ?, ?)", (ip, f"{step}:{message}"))
        conn.commit()

def get_uninstall_progress(ip: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM meta WHERE key = ?", (f"uninstall_progress_{ip}",))
        row = cursor.fetchone()
        return row['value'] if row else None

def clear_uninstall_progress(ip: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM meta WHERE key = ?", (f"uninstall_progress_{ip}",))
        conn.commit()

def get_trigger_dev_update():
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM meta WHERE key = 'trigger_dev_update'")
        row = cursor.fetchone()
        return row['value'] if row else ''

def set_trigger_dev_update(version: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('trigger_dev_update', ?)", (version,))
        conn.commit()

def clear_trigger_dev_update():
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM meta WHERE key = 'trigger_dev_update'")
        conn.commit()

def get_trigger_update_version():
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM meta WHERE key = 'trigger_update_version'")
        row = cursor.fetchone()
        return row['value'] if row else ''

def set_trigger_update_version(version: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('trigger_update_version', ?)", (version,))
        conn.commit()

def get_trigger_rollback():
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM meta WHERE key = 'trigger_rollback'")
        row = cursor.fetchone()
        return row['value'] if row else ''

def set_trigger_rollback(trigger_id: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('trigger_rollback', ?)", (trigger_id,))
        conn.commit()

def clear_trigger_rollback():
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM meta WHERE key = 'trigger_rollback'")
        conn.commit()

def set_trigger_freeze():
    with db_conn() as conn:
        cursor = conn.cursor()
        now = int(time.time())
        cursor.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('trigger_freeze', ?)", (str(now),))
        conn.commit()
        return now

def get_trigger_freeze():
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM meta WHERE key = 'trigger_freeze'")
        row = cursor.fetchone()
        return row['value'] if row else None

def clear_trigger_freeze():
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM meta WHERE key = 'trigger_freeze'")
        conn.commit()

def get_gateway_mode(ip: str) -> str:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT mode FROM gateways WHERE ip = ?', (ip,))
        row = cursor.fetchone()
        return row['mode'] if row else 'prod'

def set_gateway_mode(ip: str, mode: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE gateways SET mode = ? WHERE ip = ?', (mode, ip))
        conn.commit()

def get_gateway_zero_trust(ip: str) -> bool:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT zero_trust FROM gateways WHERE ip = ?', (ip,))
        row = cursor.fetchone()
        return bool(row and row['zero_trust'])

def set_gateway_zero_trust(ip: str, enabled: bool):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE gateways SET zero_trust = ? WHERE ip = ?', (1 if enabled else 0, ip))
        conn.commit()

def get_dev_gateways():
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM gateways WHERE mode = 'dev' ORDER BY last_seen DESC")
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

def update_gateway_windows_count(ip: str, count: int):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE gateways SET windows_count = ? WHERE ip = ?", (count, ip))
        conn.commit()

def update_gateway_heartbeat(ip: str, hostname: str, poller_ok: int, gateway_ok: int, can_reach: int):
    with db_conn() as conn:
        cursor = conn.cursor()
        now = int(time.time())
        cursor.execute('''
            UPDATE gateways SET hostname = ?, last_heartbeat = ?,
                heartbeat_poller_ok = ?, heartbeat_gateway_ok = ?, heartbeat_can_reach = ?
            WHERE ip = ?
        ''', (hostname, now, poller_ok, gateway_ok, can_reach, ip))
        conn.commit()

def set_override(ip: str, override_until: int, reason: str = ''):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE gateways SET override_until = ?, override_reason = ? WHERE ip = ?',
                       (override_until, reason, ip))
        conn.commit()

def clear_override(ip: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE gateways SET override_until = 0, override_reason = ? WHERE ip = ?',
                       ('', ip))
        conn.commit()

def get_override_active(ip: str) -> bool:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT override_until FROM gateways WHERE ip = ?', (ip,))
        row = cursor.fetchone()
        return bool(row and row['override_until'] and row['override_until'] > int(time.time()))

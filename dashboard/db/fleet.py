import json
import time
from db.core import db_conn

TERMINAL_RESULT_STATUSES = ('success', 'failed', 'timeout', 'skipped')

def init_fleet_tables(cursor):
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS fleet_commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            command TEXT NOT NULL,
            status TEXT NOT NULL,
            target_ips TEXT NOT NULL,
            origin TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            timeout INTEGER NOT NULL DEFAULT 180,
            created_at INTEGER NOT NULL,
            approved_at INTEGER DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS fleet_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cmd_id INTEGER NOT NULL,
            gateway_ip TEXT NOT NULL,
            status TEXT NOT NULL,
            exit_code INTEGER,
            output TEXT DEFAULT '',
            started_at INTEGER DEFAULT 0,
            finished_at INTEGER DEFAULT 0,
            UNIQUE (cmd_id, gateway_ip)
        )
    ''')

def create_fleet_command(command: str, target_ips, origin: str, reason: str, timeout: int):
    with db_conn() as conn:
        cursor = conn.cursor()
        now = int(time.time())
        cursor.execute('''
            INSERT INTO fleet_commands (command, status, target_ips, origin, reason, timeout, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (command, 'pending', json.dumps(target_ips), origin, reason, timeout, now))
        conn.commit()
        return cursor.lastrowid

PREVIEW_LIMIT = 2000

def _preview_result(r):
    """Trim a result's output to a small preview for the list response;
    short outputs stay inline, big ones are fetched on demand."""
    out = r.get('output') or ''
    if len(out) <= PREVIEW_LIMIT:
        r['has_more'] = False
        return r
    r['output'] = out[:PREVIEW_LIMIT] + '…'
    r['has_more'] = True
    return r

def get_fleet_commands():
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM fleet_commands ORDER BY id DESC')
        rows = cursor.fetchall()
        commands = [dict(r) for r in rows]
        for c in commands:
            try:
                c['target_ips'] = json.loads(c['target_ips'])
            except (ValueError, TypeError):
                c['target_ips'] = []
            c['results'] = [_preview_result(r) for r in get_fleet_results(c['id'])]
        return commands

def get_fleet_command(cmd_id: int):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM fleet_commands WHERE id = ?', (cmd_id,))
        row = cursor.fetchone()
        if not row:
            return None
        cmd = dict(row)
        try:
            cmd['target_ips'] = json.loads(cmd['target_ips'])
        except (ValueError, TypeError):
            cmd['target_ips'] = []
        cmd['results'] = get_fleet_results(cmd_id)
        return cmd

def set_fleet_status(cmd_id: int, status: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE fleet_commands SET status = ? WHERE id = ?', (status, cmd_id))
        conn.commit()

def approve_fleet_command(cmd_id: int):
    """Mark the command approved and create queued result rows for each target."""
    cmd = get_fleet_command(cmd_id)
    if not cmd:
        return None
    with db_conn() as conn:
        cursor = conn.cursor()
        now = int(time.time())
        cursor.execute('UPDATE fleet_commands SET status = ?, approved_at = ? WHERE id = ?',
                       ('approved', now, cmd_id))
        for ip in cmd['target_ips']:
            cursor.execute('''
                INSERT OR IGNORE INTO fleet_results (cmd_id, gateway_ip, status, started_at)
                VALUES (?, ?, 'queued', 0)
            ''', (cmd_id, ip))
        conn.commit()
    return cmd

def upsert_fleet_result(cmd_id: int, gateway_ip: str, status: str, exit_code=None, output=''):
    with db_conn() as conn:
        cursor = conn.cursor()
        now = int(time.time())
        cursor.execute('''
            INSERT INTO fleet_results (cmd_id, gateway_ip, status, exit_code, output, started_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (cmd_id, gateway_ip) DO UPDATE SET
                status = excluded.status,
                exit_code = excluded.exit_code,
                output = excluded.output,
                started_at = CASE WHEN fleet_results.started_at = 0 THEN excluded.started_at ELSE fleet_results.started_at END,
                finished_at = excluded.finished_at
        ''', (cmd_id, gateway_ip, status, exit_code, output, now, now))
        conn.commit()
    mark_fleet_complete_if_done(cmd_id)

def get_fleet_result(cmd_id: int, gateway_ip: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM fleet_results WHERE cmd_id = ? AND gateway_ip = ?', (cmd_id, gateway_ip))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_fleet_results(cmd_id: int):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM fleet_results WHERE cmd_id = ? ORDER BY gateway_ip', (cmd_id,))
        rows = cursor.fetchall()
        results = []
        for r in rows:
            item = dict(r)
            cursor.execute('SELECT hostname FROM gateways WHERE ip = ?', (item['gateway_ip'],))
            h = cursor.fetchone()
            item['hostname'] = h['hostname'] if h else None
            results.append(item)
        return results

def get_injectable_fleet_cmd(gateway_ip: str):
    """Earliest approved fleet command targeting this gateway that has no
    terminal result yet — but only if the gateway isn't already running
    another fleet command. This serializes commands per gateway in dispatch
    order (different gateways run independently)."""
    with db_conn() as conn:
        cursor = conn.cursor()
        # Busy check: any fleet command currently running on this gateway?
        cursor.execute('''
            SELECT 1 FROM fleet_results fr
            JOIN fleet_commands fc ON fc.id = fr.cmd_id
            WHERE fr.gateway_ip = ? AND fc.status = 'approved' AND fr.status = 'running'
            LIMIT 1
        ''', (gateway_ip,))
        if cursor.fetchone():
            return None
        cursor.execute('''
            SELECT id, command, timeout FROM fleet_commands fc
            WHERE fc.status = 'approved'
              AND fc.target_ips LIKE ?
              AND NOT EXISTS (
                  SELECT 1 FROM fleet_results fr
                  WHERE fr.cmd_id = fc.id AND fr.gateway_ip = ?
                    AND fr.status IN ('success', 'failed', 'timeout', 'skipped')
              )
            ORDER BY fc.id ASC LIMIT 1
        ''', (f'%"{gateway_ip}"%', gateway_ip))
        row = cursor.fetchone()
        return dict(row) if row else None

def mark_fleet_complete_if_done(cmd_id: int):
    cmd = get_fleet_command(cmd_id)
    if not cmd or cmd['status'] != 'approved':
        return
    results = cmd['results']
    if not results:
        return
    if all(r['status'] in TERMINAL_RESULT_STATUSES for r in results):
        set_fleet_status(cmd_id, 'complete')

def delete_fleet_command(cmd_id: int) -> bool:
    """Delete a fleet command and all its results — used to clear a stuck
    command (e.g. dispatched to a gateway on an old poller) so the per-gateway
    queue can move on. Returns True if a command was removed."""
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM fleet_results WHERE cmd_id = ?', (cmd_id,))
        cursor.execute('DELETE FROM fleet_commands WHERE id = ?', (cmd_id,))
        removed = cursor.rowcount > 0
        conn.commit()
        return removed

def purge_old_fleet_commands(before_ts: int) -> int:
    """Delete completed fleet commands (and their results) older than before_ts.
    Never touches approved/in-flight commands. Returns the number removed."""
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id FROM fleet_commands
            WHERE status = 'complete' AND created_at < ?
        ''', (before_ts,))
        ids = [r['id'] for r in cursor.fetchall()]
        if ids:
            placeholders = ','.join('?' * len(ids))
            cursor.execute(f'DELETE FROM fleet_results WHERE cmd_id IN ({placeholders})', ids)
            cursor.execute(f'DELETE FROM fleet_commands WHERE id IN ({placeholders})', ids)
        conn.commit()
        return len(ids)

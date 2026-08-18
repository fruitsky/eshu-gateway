import json
import time
from db.core import db_conn


def init_integrations_tables(cursor):
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS integrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            base_url TEXT NOT NULL,
            auth_type TEXT NOT NULL DEFAULT 'bearer',
            auth_header_name TEXT NOT NULL DEFAULT '',
            secret TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            kind TEXT NOT NULL DEFAULT 'custom',
            created_at INTEGER NOT NULL
        )
    ''')
    try:
        cursor.execute("ALTER TABLE integrations ADD COLUMN auth_header_name TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE integrations ADD COLUMN kind TEXT DEFAULT 'custom'")
    except Exception:
        pass
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS integration_tools (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            integration_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            method TEXT NOT NULL DEFAULT 'GET',
            path_template TEXT NOT NULL DEFAULT '',
            params TEXT NOT NULL DEFAULT '[]',
            fields TEXT NOT NULL DEFAULT '[]',
            search_field TEXT NOT NULL DEFAULT '',
            example TEXT NOT NULL DEFAULT '',
            read_only INTEGER NOT NULL DEFAULT 1,
            enabled INTEGER NOT NULL DEFAULT 1,
            UNIQUE (integration_id, name)
        )
    ''')
    try:
        cursor.execute("ALTER TABLE integration_tools ADD COLUMN fields TEXT DEFAULT '[]'")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE integration_tools ADD COLUMN search_field TEXT DEFAULT ''")
    except Exception:
        pass
    # One-time backfill: integrations seeded before the `kind` column existed
    # defaulted to 'custom'. Infer 'proxmox' for any that already carry known
    # Proxmox tool names, so existing installs don't need a manual Type edit.
    cursor.execute('''
        UPDATE integrations SET kind = 'proxmox'
        WHERE kind = 'custom'
          AND EXISTS (
              SELECT 1 FROM integration_tools t
              WHERE t.integration_id = integrations.id
                AND t.name IN ('list_nodes', 'start_vm', 'get_cluster_resources')
          )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS integration_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            integration TEXT NOT NULL,
            tool TEXT NOT NULL DEFAULT '',
            agent TEXT NOT NULL DEFAULT '',
            method TEXT NOT NULL,
            path TEXT NOT NULL,
            status_code INTEGER,
            latency_ms INTEGER,
            response_summary TEXT NOT NULL DEFAULT '',
            response_bytes INTEGER NOT NULL DEFAULT 0,
            truncated INTEGER NOT NULL DEFAULT 0,
            outcome TEXT NOT NULL DEFAULT 'ok',
            created_at INTEGER NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pending_integration_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            integration TEXT NOT NULL,
            tool TEXT NOT NULL,
            payload TEXT NOT NULL DEFAULT '{}',
            reason TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            result TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL,
            resolved_at INTEGER NOT NULL DEFAULT 0
        )
    ''')


# ── Integrations ────────────────────────────────────────────────────────

def create_integration(name: str, base_url: str, auth_type: str, secret: str,
                       auth_header_name: str = '', enabled: bool = True,
                       kind: str = 'custom') -> int:
    with db_conn() as conn:
        cursor = conn.cursor()
        now = int(time.time())
        cursor.execute('''
            INSERT INTO integrations (name, base_url, auth_type, auth_header_name, secret, enabled, kind, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (name, base_url, auth_type, auth_header_name, secret, 1 if enabled else 0, kind, now))
        conn.commit()
        return cursor.lastrowid


def get_integrations(include_secret: bool = False):
    """List integrations. Secret is server-side only — omitted unless the
    caller explicitly opts in (internal proxy path)."""
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM integrations ORDER BY name ASC')
        rows = cursor.fetchall()
        out = []
        for row in rows:
            item = dict(row)
            if not include_secret:
                item.pop('secret', None)
            out.append(item)
        return out


def get_integration(name: str):
    """Full integration row including the secret (internal use only)."""
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM integrations WHERE name = ?', (name,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_integration_by_id(integration_id: int):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM integrations WHERE id = ?', (integration_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def update_integration(name: str, base_url: str = None, auth_type: str = None,
                       secret: str = None, auth_header_name: str = None,
                       enabled: bool = None, kind: str = None) -> bool:
    """Update an integration. `secret=None` means 'leave unchanged'."""
    current = get_integration(name)
    if not current:
        return False
    new_base = base_url if base_url is not None else current['base_url']
    new_auth = auth_type if auth_type is not None else current['auth_type']
    new_header = auth_header_name if auth_header_name is not None else current.get('auth_header_name', '')
    new_secret = secret if secret is not None else current['secret']
    new_enabled = (1 if enabled else 0) if enabled is not None else current['enabled']
    new_kind = kind if kind is not None else current.get('kind', 'custom')
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE integrations SET base_url = ?, auth_type = ?, auth_header_name = ?, secret = ?, enabled = ?, kind = ?
            WHERE name = ?
        ''', (new_base, new_auth, new_header, new_secret, new_enabled, new_kind, name))
        conn.commit()
        return cursor.rowcount > 0


def delete_integration(name: str) -> bool:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM integrations WHERE name = ?', (name,))
        row = cursor.fetchone()
        if not row:
            return False
        integration_id = row['id']
        cursor.execute('DELETE FROM integration_tools WHERE integration_id = ?', (integration_id,))
        cursor.execute('DELETE FROM pending_integration_calls WHERE integration = ?', (name,))
        cursor.execute('DELETE FROM integrations WHERE id = ?', (integration_id,))
        conn.commit()
        return True


# ── Tools ───────────────────────────────────────────────────────────────

def create_tool(integration_id: int, name: str, description: str, method: str,
                path_template: str, params, example: str, read_only: bool = True,
                fields=None, search_field: str = '') -> int:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO integration_tools
                (integration_id, name, description, method, path_template, params, fields, search_field, example, read_only, enabled)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        ''', (integration_id, name, description, method, path_template,
              json.dumps(params or []), json.dumps(fields or []), search_field or '',
              example, 1 if read_only else 0))
        conn.commit()
        return cursor.lastrowid


def get_tools(integration_id: int = None):
    with db_conn() as conn:
        cursor = conn.cursor()
        if integration_id is not None:
            cursor.execute('SELECT * FROM integration_tools WHERE integration_id = ? ORDER BY name ASC', (integration_id,))
        else:
            cursor.execute('SELECT * FROM integration_tools ORDER BY name ASC')
        rows = cursor.fetchall()
        out = []
        for row in rows:
            item = dict(row)
            try:
                item['params'] = json.loads(item['params'] or '[]')
            except (ValueError, TypeError):
                item['params'] = []
            try:
                item['fields'] = json.loads(item.get('fields') or '[]')
            except (ValueError, TypeError):
                item['fields'] = []
            out.append(item)
        return out


def get_enabled_tools(integration_id: int = None):
    return [t for t in get_tools(integration_id) if t.get('enabled')]


def get_tool(integration_name: str, tool_name: str):
    integration = get_integration(integration_name)
    if not integration:
        return None
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM integration_tools WHERE integration_id = ? AND name = ?',
                       (integration['id'], tool_name))
        row = cursor.fetchone()
        if not row:
            return None
        item = dict(row)
        try:
            item['params'] = json.loads(item['params'] or '[]')
        except (ValueError, TypeError):
            item['params'] = []
        try:
            item['fields'] = json.loads(item.get('fields') or '[]')
        except (ValueError, TypeError):
            item['fields'] = []
        item['integration'] = integration
        return item


def set_tool_enabled(tool_id: int, enabled: bool) -> bool:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE integration_tools SET enabled = ? WHERE id = ?', (1 if enabled else 0, tool_id))
        conn.commit()
        return cursor.rowcount > 0


def update_tool(tool_id: int, **fields) -> bool:
    allowed = {'name', 'description', 'method', 'path_template', 'params', 'fields', 'search_field', 'example', 'read_only', 'enabled'}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return False
    if 'params' in updates:
        updates['params'] = json.dumps(updates['params'])
    if 'fields' in updates:
        updates['fields'] = json.dumps(updates['fields'])
    if 'read_only' in updates:
        updates['read_only'] = 1 if updates['read_only'] else 0
    if 'enabled' in updates:
        updates['enabled'] = 1 if updates['enabled'] else 0
    assignments = ', '.join(f'{k} = ?' for k in updates)
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(f'UPDATE integration_tools SET {assignments} WHERE id = ?',
                       tuple(updates.values()) + (tool_id,))
        conn.commit()
        return cursor.rowcount > 0


def delete_tool(tool_id: int) -> bool:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM integration_tools WHERE id = ?', (tool_id,))
        conn.commit()
        return cursor.rowcount > 0


# ── Call audit ──────────────────────────────────────────────────────────

def record_integration_call(integration: str, tool: str, agent: str, method: str,
                            path: str, status_code, latency_ms, response_summary: str,
                            response_bytes: int, truncated: int, outcome: str = 'ok'):
    with db_conn() as conn:
        cursor = conn.cursor()
        now = int(time.time())
        cursor.execute('''
            INSERT INTO integration_calls
                (integration, tool, agent, method, path, status_code, latency_ms,
                 response_summary, response_bytes, truncated, outcome, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (integration, tool, agent, method, path, status_code, latency_ms,
              response_summary, response_bytes, truncated, outcome, now))
        conn.commit()
        return cursor.lastrowid


def get_integration_calls(limit: int = 200):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM integration_calls ORDER BY id DESC LIMIT ?', (limit,))
        return [dict(row) for row in cursor.fetchall()]


# ── Pending (approval) calls ────────────────────────────────────────────

def create_pending_call(integration: str, tool: str, payload: dict, reason: str = '') -> int:
    with db_conn() as conn:
        cursor = conn.cursor()
        now = int(time.time())
        cursor.execute('''
            INSERT INTO pending_integration_calls (integration, tool, payload, reason, status, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
        ''', (integration, tool, json.dumps(payload or {}), reason, now))
        conn.commit()
        return cursor.lastrowid


def get_pending_calls(include_resolved: bool = False):
    with db_conn() as conn:
        cursor = conn.cursor()
        if include_resolved:
            cursor.execute('SELECT * FROM pending_integration_calls ORDER BY id DESC')
        else:
            cursor.execute("SELECT * FROM pending_integration_calls WHERE status = 'pending' ORDER BY id DESC")
        rows = cursor.fetchall()
        out = []
        for row in rows:
            item = dict(row)
            try:
                item['payload'] = json.loads(item['payload'] or '{}')
            except (ValueError, TypeError):
                item['payload'] = {}
            out.append(item)
        return out


def get_pending_call(call_id: int):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM pending_integration_calls WHERE id = ?', (call_id,))
        row = cursor.fetchone()
        if not row:
            return None
        item = dict(row)
        try:
            item['payload'] = json.loads(item['payload'] or '{}')
        except (ValueError, TypeError):
            item['payload'] = {}
        return item


def set_pending_call_status(call_id: int, status: str, result: str = '') -> bool:
    with db_conn() as conn:
        cursor = conn.cursor()
        now = int(time.time())
        cursor.execute('''
            UPDATE pending_integration_calls SET status = ?, result = ?, resolved_at = ?
            WHERE id = ?
        ''', (status, result, now, call_id))
        conn.commit()
        return cursor.rowcount > 0

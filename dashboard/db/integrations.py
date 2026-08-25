import json
import time
from db.core import db_conn
from core.secret_scrub import scrub_payload, secret_hashes


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
            created_at INTEGER NOT NULL,
            gate_mode TEXT NOT NULL DEFAULT 'destructive',
            mcp_mode TEXT NOT NULL DEFAULT 'joined'
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
    try:
        cursor.execute("ALTER TABLE integrations ADD COLUMN client_id TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE integrations ADD COLUMN client_secret TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE integrations ADD COLUMN token_url TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE integrations ADD COLUMN verify_tls INTEGER DEFAULT 1")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE integrations ADD COLUMN gate_mode TEXT DEFAULT 'destructive'")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE integrations ADD COLUMN mcp_mode TEXT DEFAULT 'joined'")
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
            transport TEXT NOT NULL DEFAULT 'http',
            filter_fields TEXT NOT NULL DEFAULT '[]',
            generic INTEGER NOT NULL DEFAULT 0,
            version TEXT NOT NULL DEFAULT 'v1',
            strip_envelope INTEGER NOT NULL DEFAULT 0,
            seeded INTEGER NOT NULL DEFAULT 0,
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
    try:
        cursor.execute("ALTER TABLE integration_tools ADD COLUMN transport TEXT DEFAULT 'http'")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE integration_tools ADD COLUMN filter_fields TEXT DEFAULT '[]'")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE integration_tools ADD COLUMN generic INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE integration_tools ADD COLUMN version TEXT DEFAULT 'v1'")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE integration_tools ADD COLUMN strip_envelope INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE integration_tools ADD COLUMN transform TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE integration_tools ADD COLUMN not_implemented INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE integration_tools ADD COLUMN always_gate INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE integration_tools ADD COLUMN error_codes TEXT DEFAULT '{}'")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE integration_tools ADD COLUMN path_variants TEXT DEFAULT '{}'")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE integration_tools ADD COLUMN response_hint TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE integration_tools ADD COLUMN seeded INTEGER DEFAULT 0")
    except Exception:
        pass
    # Backfill: tools seeded before the `seeded` column existed carry the column
    # default 0, so the stale-seed cleanup would skip them. Curated seed tools
    # always set at least one of transform / error_codes / always_gate /
    # response_hint / generic; hand-created tools (API create) set none — so
    # those traits reliably identify pre-migration seed tools. Idempotent.
    cursor.execute('''
        UPDATE integration_tools SET seeded = 1
        WHERE seeded = 0 AND (
            transform != '' OR error_codes != '{}' OR always_gate = 1
            OR response_hint != '' OR generic = 1
        )
    ''')
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
    try:
        cursor.execute("ALTER TABLE pending_integration_calls ADD COLUMN secret_hashes TEXT DEFAULT '{}'")
    except Exception:
        pass


# ── Integrations ────────────────────────────────────────────────────────

def create_integration(name: str, base_url: str, auth_type: str, secret: str,
                       auth_header_name: str = '', enabled: bool = True,
                       kind: str = 'custom', client_id: str = '',
                       client_secret: str = '', token_url: str = '',
                       verify_tls: bool = True, gate_mode: str = 'destructive',
                       mcp_mode: str = 'joined') -> int:
    with db_conn() as conn:
        cursor = conn.cursor()
        now = int(time.time())
        cursor.execute('''
            INSERT INTO integrations (name, base_url, auth_type, auth_header_name, secret, enabled, kind, created_at, client_id, client_secret, token_url, verify_tls, gate_mode, mcp_mode)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (name, base_url, auth_type, auth_header_name, secret, 1 if enabled else 0, kind, now,
              client_id, client_secret, token_url, 1 if verify_tls else 0, gate_mode, mcp_mode))
        conn.commit()
        return cursor.lastrowid


def _suffix(value, n: int = 4) -> str:
    """Last `n` chars of a secret for audit/UI correlation, or '' if empty.
    A short value is returned in full; otherwise it's ellipsis-prefixed."""
    if not value:
        return ''
    value = str(value)
    return value if len(value) <= n else '…' + value[-n:]


def get_integrations(include_secret: bool = False):
    """List integrations. Secret material is server-side only — omitted unless
    the caller explicitly opts in (internal proxy path). When stripped, the last
    few chars of the secrets are still exposed as `secret_suffix` /
    `client_secret_suffix` so operators can correlate which key is configured."""
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM integrations ORDER BY name ASC')
        rows = cursor.fetchall()
        out = []
        for row in rows:
            item = dict(row)
            if not include_secret:
                item['secret_suffix'] = _suffix(item.get('secret'))
                item['client_secret_suffix'] = _suffix(item.get('client_secret'))
                item.pop('secret', None)
                item.pop('client_secret', None)
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
                       enabled: bool = None, kind: str = None,
                       client_id: str = None, client_secret: str = None,
                       token_url: str = None, verify_tls: bool = None,
                       gate_mode: str = None, mcp_mode: str = None) -> bool:
    """Update an integration. `secret=None`/`client_secret=None` mean 'leave unchanged'."""
    current = get_integration(name)
    if not current:
        return False
    new_base = base_url if base_url is not None else current['base_url']
    new_auth = auth_type if auth_type is not None else current['auth_type']
    new_header = auth_header_name if auth_header_name is not None else current.get('auth_header_name', '')
    new_secret = secret if secret is not None else current['secret']
    new_enabled = (1 if enabled else 0) if enabled is not None else current['enabled']
    new_kind = kind if kind is not None else current.get('kind', 'custom')
    new_client_id = client_id if client_id is not None else current.get('client_id', '')
    new_client_secret = client_secret if client_secret is not None else current.get('client_secret', '')
    new_token_url = token_url if token_url is not None else current.get('token_url', '')
    new_verify_tls = (1 if verify_tls else 0) if verify_tls is not None else current.get('verify_tls', 1)
    new_gate_mode = gate_mode if gate_mode is not None else current.get('gate_mode', 'destructive')
    new_mcp_mode = mcp_mode if mcp_mode is not None else current.get('mcp_mode', 'joined')
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE integrations SET base_url = ?, auth_type = ?, auth_header_name = ?, secret = ?,
                enabled = ?, kind = ?, client_id = ?, client_secret = ?, token_url = ?, verify_tls = ?, gate_mode = ?, mcp_mode = ?
            WHERE name = ?
        ''', (new_base, new_auth, new_header, new_secret, new_enabled, new_kind,
              new_client_id, new_client_secret, new_token_url, new_verify_tls, new_gate_mode, new_mcp_mode, name))
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
                fields=None, search_field: str = '', transport: str = 'http',
                filter_fields=None, generic: bool = False, version: str = 'v1',
                strip_envelope: bool = False, transform: str = '',
                not_implemented: bool = False, always_gate: bool = False,
                error_codes: dict = None, path_variants: dict = None,
                response_hint: str = '', seeded: bool = False) -> int:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO integration_tools
                (integration_id, name, description, method, path_template, params, fields, search_field, example, read_only, enabled, transport, filter_fields, generic, version, strip_envelope, transform, not_implemented, always_gate, error_codes, path_variants, response_hint, seeded)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (integration_id, name, description, method, path_template,
              json.dumps(params or []), json.dumps(fields or []), search_field or '',
              example, 1 if read_only else 0,
              transport or 'http', json.dumps(filter_fields or []), 1 if generic else 0,
              version or 'v1', 1 if strip_envelope else 0,
              transform or '', 1 if not_implemented else 0,
              1 if always_gate else 0, json.dumps(error_codes or {}),
              json.dumps(path_variants or {}), response_hint or '',
              1 if seeded else 0))
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
            try:
                item['filter_fields'] = json.loads(item.get('filter_fields') or '[]')
            except (ValueError, TypeError):
                item['filter_fields'] = []
            try:
                item['error_codes'] = json.loads(item.get('error_codes') or '{}')
            except (ValueError, TypeError):
                item['error_codes'] = {}
            try:
                item['path_variants'] = json.loads(item.get('path_variants') or '{}')
            except (ValueError, TypeError):
                item['path_variants'] = {}
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
        try:
            item['filter_fields'] = json.loads(item.get('filter_fields') or '[]')
        except (ValueError, TypeError):
            item['filter_fields'] = []
        try:
            item['error_codes'] = json.loads(item.get('error_codes') or '{}')
        except (ValueError, TypeError):
            item['error_codes'] = {}
        try:
            item['path_variants'] = json.loads(item.get('path_variants') or '{}')
        except (ValueError, TypeError):
            item['path_variants'] = {}
        item['integration'] = integration
        return item


def set_tool_enabled(tool_id: int, enabled: bool) -> bool:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE integration_tools SET enabled = ? WHERE id = ?', (1 if enabled else 0, tool_id))
        conn.commit()
        return cursor.rowcount > 0


def set_all_tools_enabled(integration_id: int, enabled: bool) -> int:
    """Enable/disable every tool for an integration. Returns the number of tools
    updated. This is the bulk lever for trimming the MCP-visible tool set."""
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE integration_tools SET enabled = ? WHERE integration_id = ?',
                       (1 if enabled else 0, integration_id))
        conn.commit()
        return cursor.rowcount


def update_tool(tool_id: int, **fields) -> bool:
    allowed = {'name', 'description', 'method', 'path_template', 'params', 'fields', 'search_field', 'example', 'read_only', 'enabled', 'transport', 'filter_fields', 'generic', 'version', 'strip_envelope', 'transform', 'not_implemented', 'always_gate', 'error_codes', 'path_variants', 'response_hint', 'seeded'}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return False
    if 'params' in updates:
        updates['params'] = json.dumps(updates['params'])
    if 'fields' in updates:
        updates['fields'] = json.dumps(updates['fields'])
    if 'filter_fields' in updates:
        updates['filter_fields'] = json.dumps(updates['filter_fields'])
    if 'error_codes' in updates:
        updates['error_codes'] = json.dumps(updates['error_codes'] or {})
    if 'path_variants' in updates:
        updates['path_variants'] = json.dumps(updates['path_variants'] or {})
    if 'response_hint' in updates:
        updates['response_hint'] = updates['response_hint'] or ''
    if 'read_only' in updates:
        updates['read_only'] = 1 if updates['read_only'] else 0
    if 'enabled' in updates:
        updates['enabled'] = 1 if updates['enabled'] else 0
    if 'generic' in updates:
        updates['generic'] = 1 if updates['generic'] else 0
    if 'strip_envelope' in updates:
        updates['strip_envelope'] = 1 if updates['strip_envelope'] else 0
    if 'not_implemented' in updates:
        updates['not_implemented'] = 1 if updates['not_implemented'] else 0
    if 'always_gate' in updates:
        updates['always_gate'] = 1 if updates['always_gate'] else 0
    if 'seeded' in updates:
        updates['seeded'] = 1 if updates['seeded'] else 0
    if updates.get('transform') is not None:
        updates['transform'] = updates['transform'] or ''
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


def get_integration_calls(search: str = None, start: int = None, end: int = None,
                          limit: int = 50, offset: int = 0):
    """Paginated, filterable list of proxied calls, newest first.

    `search` is a case-insensitive LIKE across the readable columns. `start`/`end`
    are epoch-second bounds (inclusive start, exclusive end) — the frontend
    computes these from the viewer's local-time day boundaries. Returns
    `{"rows": [...], "total": N}` so the UI can paginate."""
    where = []
    params = []
    if search:
        needle = '%' + search + '%'
        where.append('(integration LIKE ? OR tool LIKE ? OR agent LIKE ?'
                     ' OR method LIKE ? OR path LIKE ? OR outcome LIKE ?'
                     ' OR CAST(status_code AS TEXT) LIKE ?)')
        params += [needle] * 7
    if start is not None:
        where.append('created_at >= ?')
        params.append(start)
    if end is not None:
        where.append('created_at < ?')
        params.append(end)
    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(f'SELECT COUNT(*) FROM integration_calls {where_sql}', params)
        total = cursor.fetchone()[0]
        cursor.execute(
            f'SELECT * FROM integration_calls {where_sql} ORDER BY id DESC LIMIT ? OFFSET ?',
            params + [limit, offset])
        rows = [dict(row) for row in cursor.fetchall()]
    return {'rows': rows, 'total': total}


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
            try:
                item['secret_hashes'] = json.loads(item.get('secret_hashes') or '{}')
            except (ValueError, TypeError):
                item['secret_hashes'] = {}
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
        try:
            item['secret_hashes'] = json.loads(item.get('secret_hashes') or '{}')
        except (ValueError, TypeError):
            item['secret_hashes'] = {}
        return item


def set_pending_call_status(call_id: int, status: str, result: str = '') -> bool:
    """Set a pending call's status. On resolution (approved/denied) any
    secret-keyed values still in the stored payload are replaced with
    '[redacted]' and their SHA-256 fingerprints moved to `secret_hashes`, so a
    credential is never persisted beyond the submit→decide window. Rows already
    processed (non-empty secret_hashes) are left alone, keeping the fingerprints
    stable across re-runs (e.g. the startup migration)."""
    current = get_pending_call(call_id)
    if not current:
        return False
    now = int(time.time())
    payload_json = None
    hashes_json = None
    if current.get('payload') and not current.get('secret_hashes'):
        hashes = secret_hashes(current['payload'])
        if hashes:
            payload_json = json.dumps(scrub_payload(current['payload']))
            hashes_json = json.dumps(hashes)
    with db_conn() as conn:
        cursor = conn.cursor()
        if payload_json is None:
            cursor.execute('''
                UPDATE pending_integration_calls SET status = ?, result = ?, resolved_at = ?
                WHERE id = ?
            ''', (status, result, now, call_id))
        else:
            cursor.execute('''
                UPDATE pending_integration_calls
                SET status = ?, result = ?, resolved_at = ?, payload = ?, secret_hashes = ?
                WHERE id = ?
            ''', (status, result, now, payload_json, hashes_json, call_id))
        conn.commit()
        return cursor.rowcount > 0


def strip_resolved_payloads() -> int:
    """Startup migration: strip raw payloads (keeping SHA-256 fingerprints)
    from any already-resolved pending call, so credentials persisted by older
    versions are purged on the next deploy. Idempotent."""
    resolved = [c for c in get_pending_calls(include_resolved=True)
                if c['status'] in ('approved', 'denied') and c.get('payload')]
    count = 0
    for call in resolved:
        if set_pending_call_status(call['id'], call['status'], call.get('result') or ''):
            count += 1
    return count


def purge_old_integration_calls(cutoff_ts: int) -> int:
    """Delete MCP audit + approval rows older than `cutoff_ts` (both stores).
    Returns the total number of rows removed."""
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM integration_calls WHERE created_at < ?', (cutoff_ts,))
        n1 = cursor.rowcount
        cursor.execute('DELETE FROM pending_integration_calls WHERE created_at < ?', (cutoff_ts,))
        n2 = cursor.rowcount
        conn.commit()
        return n1 + n2


def mask_sensitive_args(payload: dict, tool: dict) -> dict:
    """Return a copy of a call payload with `redact`-flagged param values
    replaced by '[redacted]'.

    Used for audit/UI display (pending-call listings, dashboard history). The
    stored payload is left intact so an approved call still executes with the
    real arguments."""
    redacted = {p['name'] for p in (tool.get('params') or [])
                if isinstance(p, dict) and p.get('redact')}
    if not redacted:
        return payload
    out = dict(payload)
    for key in redacted:
        if key in out and out[key]:
            out[key] = '[redacted]'
    return out

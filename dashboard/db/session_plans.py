"""Session plans (F3): the operator approves a list of exact commands once.

A plan belongs to a minted session. Items are exact commands per host:
``{"host": "<gateway ip>" or "*", "command": "<exact text>", "max_uses": 1}``.
Matching is **byte-exact after trimming whitespace** — no patterns, no
prefixes — so the chunk 2a and 1c guarantees (metacharacter JIT, exact
windows) stay intact.

Lifecycle: ``proposed -> approved | denied -> expired | ended``. One active
plan per session: a new proposal replaces an existing ``proposed`` one, and
approving a plan ends any other approved plan for the session.

The approval path claims a use with a single conditional UPDATE
(``uses = uses + 1 WHERE uses < max_uses``), so two simultaneous requests for
the same last use cannot both be approved.
"""
import ipaddress
import time

from db.core import db_conn

import re

MAX_ITEMS = 50
MAX_COMMAND_BYTES = 4096
# P1: only these curated Supervisor actions may be covered by an agent's MCP
# plan item. The generic read/write, `call_service`, `ws_write` and
# `ha_host_reboot` never can; the reboot always needs its own card (S1 follows
# from this allowlist).
PLAN_COVERABLE_TOOLS = frozenset({
    'ha_addon_action', 'ha_addon_update', 'ha_core_update',
    'ha_backup_create', 'ha_core_restart',
})
MAX_ARGS_DISPLAY = 2000
# F10: an `mcp` item names one integration tool; the hash is SHA-256 hex.
TOOL_RE = re.compile(r'^[A-Za-z0-9_.:-]{1,120}$')
HASH_RE = re.compile(r'^[0-9a-f]{64}$')
MAX_NOTE = 500
DEFAULT_TTL_MINUTES = 60
MAX_TTL_MINUTES = 240
MAX_USES = 100


def init_session_plans_tables(cursor):
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS session_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'proposed',
            created_at INTEGER NOT NULL,
            decided_at INTEGER NOT NULL DEFAULT 0,
            decided_by TEXT NOT NULL DEFAULT '',
            until INTEGER NOT NULL DEFAULT 0,
            note TEXT NOT NULL DEFAULT ''
        )
    ''')
    # F15/AG1: who created the plan. A probe plan may cover a command for an
    # agent in `approval` mode (the operator approved the probe's exact bytes
    # once, through the probe itself); an agent-proposed plan may not.
    _cols = {row[1] for row in cursor.execute(
        "PRAGMA table_info(session_plans)").fetchall()}
    if 'origin' not in _cols:
        try:
            cursor.execute(
                "ALTER TABLE session_plans ADD COLUMN origin TEXT NOT NULL DEFAULT 'agent'")
        except Exception:
            pass
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS session_plan_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL,
            host TEXT NOT NULL DEFAULT '*',
            command TEXT NOT NULL,
            max_uses INTEGER NOT NULL DEFAULT 1,
            uses INTEGER NOT NULL DEFAULT 0,
            removed INTEGER NOT NULL DEFAULT 0
        )
    ''')
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_session_plans_session '
        'ON session_plans(session_id, status)')
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_session_plan_items_plan '
        'ON session_plan_items(plan_id)')
    # F10: a plan item is either an SSH command (kind='ssh', the default and
    # every pre-F10 row) or one MCP tool call (kind='mcp') identified by the
    # exact argument hash the agent proposed. `command` stays the human label
    # the plan UI already renders.
    # P1: `args_json` is the redacted display copy the plan card shows (the
    # match still uses `args_hash`), and `summary` is the handler preflight
    # summary when there is one.
    for col, default in [('kind', "'ssh'"), ('integration', "''"),
                         ('tool', "''"), ('args_hash', "''"),
                         ('args_json', "''"), ('summary', "''")]:
        try:
            cursor.execute(f"ALTER TABLE session_plan_items ADD COLUMN {col} TEXT DEFAULT {default}")
        except Exception:
            pass


def _forbidden_command_char(command: str):
    """SR1: the one character class a plan item may not contain.

    Matching is byte-exact, but `str.strip()` removes far more than the
    gateway's `[[:space:]]` trim (C0 separators, DEL, `\xa0`, `\u2028`, …), so
    those bytes could hide a mismatch. Items may contain printable text, plain
    spaces and tabs — nothing else.
    """
    for ch in command:
        if ch in (' ', '\t'):
            continue
        if ord(ch) < 0x20 or ord(ch) == 0x7f or ch.isspace():
            return ch
    return None


class PlanError(ValueError):
    """A plan operation the caller should see as a stable error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def args_hash(args) -> str:
    """Stable hash of one MCP call's arguments (sorted keys, compact JSON).

    The plan stores the hash, never the raw arguments: secrets in an argument
    (an HA service payload can carry a token) must not be copied into a plan
    row that the UI renders."""
    import hashlib
    import json
    try:
        body = json.dumps(args or {}, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=False)
    except (TypeError, ValueError):
        raise PlanError('invalid_args', 'args must be JSON-serialisable')
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


def _is_ha_integration(name: str) -> bool:
    """R2: is `name` an integration of kind 'ha'? Unknown names are not.

    The kind is read from the integrations row, never taken from the caller:
    an item stores the integration *name*, and that name is what the claim
    matches, so the kind has to come from the same row."""
    with db_conn() as conn:
        row = conn.execute('SELECT kind FROM integrations WHERE name = ?',
                           (name,)).fetchone()
    return bool(row) and (row['kind'] or '').strip().lower() == 'ha'


def _validate_mcp_item(item):
    integration = str(item.get('integration') or '').strip()
    tool = str(item.get('tool') or '').strip()
    if not integration or not tool:
        raise PlanError('invalid_items',
                        "an mcp item needs 'integration' and 'tool'")
    if not TOOL_RE.match(integration) or not TOOL_RE.match(tool):
        raise PlanError('invalid_items',
                        'integration and tool must be plain names')
    if tool not in PLAN_COVERABLE_TOOLS:
        # P1: an allowlist, not one excluded tool. The operator must be able to
        # read what a plan item does; the generic tools take an arbitrary path
        # and body, so they can never be covered by a plan.
        raise PlanError('not_plan_coverable',
                        '%s cannot be covered by a plan; only the curated '
                        'Supervisor actions can be' % tool)
    raw = item.get('args')
    if not isinstance(raw, dict):
        # P1: a bare args_hash hid the arguments from the operator.
        raise PlanError('invalid_items',
                        "an mcp item needs its 'args', not only an args_hash")
    digest = args_hash(raw)
    supplied = item.get('args_hash')
    if supplied is not None and str(supplied).strip().lower() != digest:
        raise PlanError('invalid_items',
                        'args_hash does not match args')
    max_uses = item.get('max_uses', 1)
    if isinstance(max_uses, bool) or not isinstance(max_uses, int):
        raise PlanError('invalid_max_uses', 'max_uses must be an integer')
    if max_uses < 1 or max_uses > MAX_USES:
        raise PlanError('invalid_max_uses',
                        f'max_uses must be between 1 and {MAX_USES}')
    if not _is_ha_integration(integration):
        # R2: the allowlist above is keyed by tool name, and any integration
        # can register a tool with the same name. F16's V6 rule keys on
        # (kind, tool), so an mcp item must name an integration of kind 'ha'.
        # Checked here and again in claim_mcp_plan_item.
        raise PlanError('not_plan_coverable',
                        '%s is not a Home Assistant integration; its tools '
                        'cannot be covered by a plan' % integration)
    # P1: the card shows the arguments with secret-looking keys redacted, and
    # the handler's preflight summary when it has one. Both are display only;
    # the call still matches on the hash of the raw arguments.
    display, summary = _mcp_display(integration, tool, raw)
    if len(display.encode('utf-8')) > MAX_ARGS_DISPLAY:
        # Nit (K1 review): the card used to truncate the display while the
        # match used the full arguments, so an operator could approve a tail
        # they never saw. Refuse instead; the curated actions take short
        # arguments.
        raise PlanError('invalid_args',
                        'the argument display is longer than %d bytes; shorten '
                        'the arguments so the operator can see all of them'
                        % MAX_ARGS_DISPLAY)
    label = f"mcp: {integration}.{tool} ({digest[:12]})"
    return {'kind': 'mcp', 'host': '*', 'command': label,
            'integration': integration, 'tool': tool, 'args_hash': digest,
            'args_json': display, 'summary': summary, 'max_uses': max_uses}


def _mcp_display(integration_name: str, tool_name: str, args: dict):
    """P1: (redacted args JSON, preflight summary) for the plan card."""
    import json as _json
    try:
        from core.secret_scrub import scrub_payload
        display = _json.dumps(scrub_payload(args or {}), sort_keys=True,
                              separators=(',', ':'))
    except Exception:
        display = ''
    summary = ''
    try:
        from core.tool_handlers import run_preflight
        from db.integrations import get_integration, get_tool
        integration = get_integration(integration_name)
        tool = get_tool(integration_name, tool_name)
        if integration and tool and tool.get('handler'):
            pre = run_preflight(tool['handler'], integration, tool, args or {})
            if isinstance(pre, dict):
                summary = str(pre.get('_summary') or '')[:300]
    except Exception:
        summary = ''
    return display, summary


def validate_items(items):
    """Normalise and validate the proposed items; raises PlanError."""
    if not isinstance(items, (list, tuple)) or not items:
        raise PlanError('invalid_items', 'items must be a non-empty list')
    if len(items) > MAX_ITEMS:
        raise PlanError('too_many_items',
                        f'a plan holds at most {MAX_ITEMS} items')
    normalised = []
    for item in items:
        if not isinstance(item, dict):
            raise PlanError('invalid_items', 'each item must be an object')
        kind = str(item.get('kind') or 'ssh').strip().lower()
        if kind == 'mcp':
            normalised.append(_validate_mcp_item(item))
            continue
        if kind != 'ssh':
            raise PlanError('invalid_items', "item kind must be 'ssh' or 'mcp'")
        command = item.get('command')
        if not isinstance(command, str) or not command.strip():
            raise PlanError('invalid_command', 'each item needs a command')
        # SR1: inspect the raw text first — `strip()` itself would silently
        # remove `\x1c`, `\xa0`, `\u2028`, … and hide them from the check.
        if _forbidden_command_char(command) is not None:
            raise PlanError('invalid_command',
                            'commands may not contain control characters or '
                            'non-ASCII whitespace (plain spaces and tabs only)')
        command = command.strip()
        if len(command.encode('utf-8')) > MAX_COMMAND_BYTES:
            raise PlanError('command_too_long',
                            f'each command is at most {MAX_COMMAND_BYTES} bytes')
        host = (item.get('host') or '*').strip() or '*'
        if host != '*':
            try:
                ipaddress.ip_address(host)
            except ValueError:
                raise PlanError('invalid_host',
                                'host must be "*" or an IP address')
        max_uses = item.get('max_uses', 1)
        if isinstance(max_uses, bool) or not isinstance(max_uses, int):
            raise PlanError('invalid_max_uses', 'max_uses must be an integer')
        if max_uses < 1 or max_uses > MAX_USES:
            raise PlanError('invalid_max_uses',
                            f'max_uses must be between 1 and {MAX_USES}')
        normalised.append({'kind': 'ssh', 'host': host, 'command': command,
                           'integration': '', 'tool': '', 'args_hash': '',
                           'max_uses': max_uses})
    return normalised


def _row_to_plan(row, items):
    plan = dict(row)
    plan['items'] = [dict(item) for item in items]
    return plan


def _items_for(conn, plan_id):
    return conn.execute(
        'SELECT * FROM session_plan_items WHERE plan_id = ? ORDER BY id',
        (plan_id,)).fetchall()


def create_plan(session_id: str, items, note: str = '', origin: str = 'agent') -> dict:
    """Store a new proposed plan, replacing any other proposal for the session."""
    sid = (session_id or '').strip()[:64]
    if not sid:
        raise PlanError('invalid_session', 'session_id is required')
    origin = origin if origin in ('agent', 'probe', 'recipe') else 'agent'
    normalised = validate_items(items)
    if origin != 'agent':
        # AG1 condition 2: server plans are closed and specific. An item that
        # ranges over every host is an agent-style plan.
        for item in normalised:
            if item.get('host') in ('', '*'):
                raise PlanError(
                    'invalid_host',
                    'a plan created by %s must name a specific host' % origin)
    note = (note or '')[:MAX_NOTE]
    now = int(time.time())
    with db_conn() as conn:
        cur = conn.cursor()
        # One proposal per session: drop the previous proposed plan (its audit
        # event stays in the log).
        old = [row['id'] for row in cur.execute(
            "SELECT id FROM session_plans WHERE session_id = ? AND status = 'proposed'",
            (sid,)).fetchall()]
        for plan_id in old:
            cur.execute('DELETE FROM session_plan_items WHERE plan_id = ?', (plan_id,))
            cur.execute('DELETE FROM session_plans WHERE id = ?', (plan_id,))
        cur.execute('''
            INSERT INTO session_plans (session_id, status, created_at, note, origin)
            VALUES (?, 'proposed', ?, ?, ?)
        ''', (sid, now, note, origin))
        plan_id = cur.lastrowid
        for item in normalised:
            cur.execute('''
                INSERT INTO session_plan_items
                    (plan_id, host, command, max_uses, uses, removed,
                     kind, integration, tool, args_hash, args_json, summary)
                VALUES (?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?)
            ''', (plan_id, item['host'], item['command'], item['max_uses'],
                    item.get('kind') or 'ssh', item.get('integration') or '',
                    item.get('tool') or '', item.get('args_hash') or '',
                    item.get('args_json') or '', item.get('summary') or ''))
        conn.commit()
    hosts = {item['host'] for item in normalised}
    return {'plan_id': plan_id, 'status': 'proposed', 'session_id': sid,
            'items': len(normalised), 'hosts': len(hosts)}


def get_plan(plan_id: int):
    with db_conn() as conn:
        row = conn.execute('SELECT * FROM session_plans WHERE id = ?',
                           (int(plan_id),)).fetchone()
        if not row:
            return None
        return _row_to_plan(row, _items_for(conn, row['id']))


def list_plans(status: str = None, session_id: str = None, limit: int = 50) -> list:
    """Plans newest-first, optionally by status and/or session."""
    sql = 'SELECT * FROM session_plans'
    where = []
    params = []
    if status:
        where.append('status = ?')
        params.append(status)
    if session_id:
        where.append('session_id = ?')
        params.append(session_id)
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY id DESC LIMIT ?'
    params.append(int(limit))
    with db_conn() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
        return [_row_to_plan(row, _items_for(conn, row['id'])) for row in rows]


def get_active_plan(session_id: str, now: int = None):
    """The session's approved, unexpired plan (and its items), or None."""
    sid = (session_id or '').strip()[:64]
    if not sid:
        return None
    now = int(now or time.time())
    expire_plans(now)
    with db_conn() as conn:
        row = conn.execute('''
            SELECT * FROM session_plans
            WHERE session_id = ? AND status = 'approved' AND until > ?
            ORDER BY id DESC LIMIT 1
        ''', (sid, now)).fetchone()
        if not row:
            return None
        return _row_to_plan(row, _items_for(conn, row['id']))


def expire_plans(now: int = None):
    """Mark approved plans whose TTL ran out as expired. Idempotent."""
    now = int(now or time.time())
    with db_conn() as conn:
        conn.execute('''
            UPDATE session_plans SET status = 'expired'
            WHERE status = 'approved' AND until <= ?
        ''', (now,))
        conn.commit()


def approve_plan(plan_id: int, ttl_minutes: int = DEFAULT_TTL_MINUTES,
                 removed_item_ids=(), decided_by: str = 'operator') -> dict:
    """Approve a proposed plan (optionally removing items); returns the plan."""
    plan_id = int(plan_id)
    try:
        ttl_minutes = int(ttl_minutes if ttl_minutes is not None else DEFAULT_TTL_MINUTES)
    except (TypeError, ValueError):
        raise PlanError('invalid_ttl', 'ttl_minutes must be an integer')
    if ttl_minutes < 1 or ttl_minutes > MAX_TTL_MINUTES:
        raise PlanError('invalid_ttl',
                        f'ttl_minutes must be between 1 and {MAX_TTL_MINUTES}')
    now = int(time.time())
    removed = {int(item_id) for item_id in (removed_item_ids or [])}
    with db_conn() as conn:
        cur = conn.cursor()
        plan = cur.execute('SELECT * FROM session_plans WHERE id = ?',
                           (plan_id,)).fetchone()
        if not plan:
            raise PlanError('not_found', 'plan not found')
        if plan['status'] != 'proposed':
            raise PlanError('not_proposed',
                            f"plan is {plan['status']}, not proposed")
        kept = 0
        for item in _items_for(conn, plan_id):
            if item['id'] in removed:
                cur.execute('UPDATE session_plan_items SET removed = 1 WHERE id = ?',
                            (item['id'],))
            else:
                kept += 1
        if not kept:
            raise PlanError('no_items', 'every item was removed — nothing to approve')
        # One active plan per session: a fresh approval supersedes an older one.
        cur.execute('''
            UPDATE session_plans SET status = 'ended', decided_at = ?
            WHERE session_id = ? AND status = 'approved' AND id != ?
        ''', (now, plan['session_id'], plan_id))
        cur.execute('''
            UPDATE session_plans
            SET status = 'approved', decided_at = ?, decided_by = ?, until = ?
            WHERE id = ? AND status = 'proposed'
        ''', (now, (decided_by or 'operator')[:64], now + ttl_minutes * 60, plan_id))
        conn.commit()
    return get_plan(plan_id)


_DECIDABLE_FROM = {
    'denied': ('proposed',),            # Deny is for a pending proposal only.
    'ended': ('proposed', 'approved'),  # End also withdraws a pending proposal.
}


def deny_plan(plan_id: int, decided_by: str = 'operator') -> dict:
    return _decide(plan_id, 'denied', decided_by)


def end_plan(plan_id: int, decided_by: str = 'operator') -> dict:
    """End an approved (or withdraw a proposed) plan early."""
    return _decide(plan_id, 'ended', decided_by)


def _decide(plan_id: int, status: str, decided_by: str) -> dict:
    plan_id = int(plan_id)
    now = int(time.time())
    with db_conn() as conn:
        cur = conn.cursor()
        plan = cur.execute('SELECT * FROM session_plans WHERE id = ?',
                           (plan_id,)).fetchone()
        if not plan:
            raise PlanError('not_found', 'plan not found')
        # Never rewrite a decided outcome: denying an approved plan (or ending
        # an expired/denied one) must not look like it changed anything.
        if plan['status'] not in _DECIDABLE_FROM[status]:
            raise PlanError('bad_state',
                            f"cannot {status} a plan that is {plan['status']}")
        cur.execute('''
            UPDATE session_plans SET status = ?, decided_at = ?, decided_by = ?
            WHERE id = ?
        ''', (status, now, (decided_by or 'operator')[:64], plan_id))
        conn.commit()
    return get_plan(plan_id)


def _claim_candidate(conn, session_id: str, host: str, command: str, now: int,
                     origins=None, agent=None):
    """The first item of the session's active plan that still has a use left.

    AG1: `origins` narrows the claim to plans created by that code (a probe in
    `approval` mode claims only its own plans)."""
    sql = '''
        SELECT i.id, i.plan_id
        FROM session_plan_items i
        JOIN session_plans p ON p.id = i.plan_id
        WHERE p.session_id = ? AND p.status = 'approved' AND p.until > ?
          AND i.removed = 0 AND i.uses < i.max_uses
          AND i.kind = 'ssh'
          AND i.command = ? AND (i.host = ? OR i.host = '*')
    '''
    params = [session_id, now, command, host]
    if origins:
        marks = ','.join('?' for _ in origins)
        sql += ' AND p.origin IN (%s)' % marks
        params.extend(origins)
    if agent:
        # AG1 condition 3: F10's M2 rule for SSH plan claims. Someone holding
        # another session's id must not spend its probe items.
        sql += (' AND EXISTS (SELECT 1 FROM sessions s '
                'WHERE s.id = p.session_id AND s.agent = ?)')
        params.append(agent)
    sql += ' ORDER BY i.id LIMIT 1'
    return conn.execute(sql, tuple(params)).fetchone()


def _claim_mcp_candidate(conn, session_id: str, owner: str, integration: str,
                         tool: str, digest: str, now: int):
    # F10 M2: the caller must own the session. The join makes this the same
    # statement as the claim, so a concurrent ownership change cannot slip
    # between the check and the use. An ownerless session matches nothing.
    return conn.execute('''
        SELECT i.id, i.plan_id
        FROM session_plan_items i
        JOIN session_plans p ON p.id = i.plan_id
        JOIN sessions s ON s.id = p.session_id
        WHERE p.session_id = ? AND p.status = 'approved' AND p.until > ?
          AND s.agent != '' AND s.agent = ?
          AND i.removed = 0 AND i.uses < i.max_uses
          AND i.kind = 'mcp' AND i.integration = ? AND i.tool = ?
          AND i.args_hash = ?
        ORDER BY i.id
        LIMIT 1
    ''', (session_id, now, owner, integration, tool, digest)).fetchone()


def claim_mcp_plan_item(session_id: str, integration: str, tool: str,
                        digest: str, owner: str = '', now: int = None):
    """F10: consume one use of an approved `mcp` plan item.

    Matching is by integration + tool + the exact argument hash, never by a
    pattern: an approved plan covers the call the operator saw, not a family of
    calls. Same conditional UPDATE as the SSH claim.

    M2: the session's owner must be the calling agent. An MCP caller can name
    any session id, so without this agent B could spend agent A's approved
    "update add-on X" item. An ownerless session matches nothing (F3 SR2).

    R1: the tool is re-checked against the allowlist here, not only in
    `create_plan`. R2: so is the integration's kind. `create_plan` is the only
    writer of `mcp` items today, so both are defence in depth — a future
    insert path must not be able to widen what an approved plan can spend."""
    sid = (session_id or '').strip()[:64]
    owner = (owner or '').strip()[:64]
    if not sid or not integration or not tool or not digest or not owner:
        return None
    if tool not in PLAN_COVERABLE_TOOLS:
        return None
    if not _is_ha_integration(integration):
        return None
    now = int(now or time.time())
    expire_plans(now)
    with db_conn() as conn:
        cur = conn.cursor()
        row = _claim_mcp_candidate(conn, sid, owner, integration, tool, digest, now)
        if not row:
            return None
        cur.execute('''
            UPDATE session_plan_items SET uses = uses + 1
            WHERE id = ? AND removed = 0 AND uses < max_uses
              AND plan_id IN (
                  SELECT id FROM session_plans
                  WHERE status = 'approved' AND until > ?
              )
        ''', (row['id'], now))
        if cur.rowcount != 1:
            return None
        conn.commit()
        return row['plan_id'], row['id']


def claim_plan_item(session_id: str, host: str, command: str, now: int = None,
                    origins=None, agent=''):
    """Atomically consume one use of a matching item.

    Returns ``(plan_id, item_id)`` when a use was consumed, else None. The
    UPDATE is conditional on ``uses < max_uses`` so a concurrent request for
    the same last use cannot also be approved.
    """
    sid = (session_id or '').strip()[:64]
    if not sid:
        return None
    now = int(now or time.time())
    host = (host or '').strip()
    # SR1: compare exactly what the gateway sent — it already trims surrounding
    # whitespace, and stripping here would let a trailing C0 byte match.
    command = command or ''
    expire_plans(now)
    with db_conn() as conn:
        cur = conn.cursor()
        row = _claim_candidate(conn, sid, host, command, now, origins=origins,
                               agent=(agent or '').strip()[:64])
        if not row:
            return None
        # The plan's state is rechecked in the same statement: End or Deny can
        # land between the SELECT above and this UPDATE, and must still stop it.
        cur.execute('''
            UPDATE session_plan_items SET uses = uses + 1
            WHERE id = ? AND removed = 0 AND uses < max_uses
              AND plan_id IN (
                  SELECT id FROM session_plans
                  WHERE status = 'approved' AND until > ?
              )
        ''', (row['id'], now))
        if cur.rowcount != 1:
            # Another request took the last use between the SELECT and here.
            conn.rollback()
            return None
        conn.commit()
        return (row['plan_id'], row['id'])

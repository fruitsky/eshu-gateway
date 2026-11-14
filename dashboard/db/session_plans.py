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
import json
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
# F22: a check plan borrows the parent's approval for at most ten minutes.
CHECK_TTL_SECONDS = 600
OPEN_CHECK_STATES = ('pending', 'running')
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
            note TEXT NOT NULL DEFAULT '',
            parent_plan_id INTEGER NOT NULL DEFAULT 0,
            stopped_at INTEGER NOT NULL DEFAULT 0,
            stopped_reason TEXT NOT NULL DEFAULT ''
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
    # X6/D7: a proposal replaced by a newer one is kept as `superseded` (with
    # the new plan's id) so check_plan/probe_result can say what happened
    # instead of "not found".
    if 'superseded_by' not in _cols:
        try:
            cursor.execute(
                "ALTER TABLE session_plans ADD COLUMN superseded_by INTEGER NOT NULL DEFAULT 0")
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
                         ('args_json', "''"), ('summary', "''"),
                         ('check_json', "''"), ('check_state', "''"),
                         ('check_reason', "''"), ('check_observed', "''"),
                         ('check_expected', "''"), ('check_token', "''")]:
        try:
            cursor.execute(f"ALTER TABLE session_plan_items ADD COLUMN {col} TEXT DEFAULT {default}")
        except Exception:
            pass
    # F22: the check's numeric bookkeeping. `check_plan_id`/`check_item_id`
    # name the one-item plan the check borrows (AN2); `check_request_id` is
    # the approval-mode card link (AR2); `check_attempts` is the bound (AN5).
    for col, default in [('check_exit_code', 0), ('check_attempts', 0),
                         ('check_at', 0), ('check_plan_id', 0),
                         ('check_item_id', 0), ('check_request_id', 0),
                         ('parent_item_id', 0)]:
        try:
            cursor.execute(
                f"ALTER TABLE session_plan_items ADD COLUMN {col} INTEGER DEFAULT {default}")
        except Exception:
            pass
    # F22 §10: the parent plan's new state and the link a check plan names.
    for col, default in [('parent_plan_id', 0), ('stopped_at', 0),
                         ('stopped_reason', "''")]:
        try:
            cursor.execute(
                f"ALTER TABLE session_plans ADD COLUMN {col} "
                + ("INTEGER DEFAULT 0" if default == 0 else "TEXT DEFAULT ''"))
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


# F22/AN2: the private sentinel `create_plan` requires for a `check` origin.
# A boolean could be forwarded from stored data; an object cannot, and only
# `core/checks.py` imports it.
CHECK_INTERNAL = object()


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
            'args_json': display, 'summary': summary, 'max_uses': max_uses,
            'check_json': _check_json(item.get('check'))}


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
                           'max_uses': max_uses,
                           'check_json': _check_json(item.get('check'))})
    return normalised


def _check_json(raw):
    """F22: validate one item's check and store it canonically, or ''.

    A check on an item is the operator's approved follow-up, so its shape is
    closed and its bytes are checked here (the read policy is checked here and
    again at run time)."""
    if raw is None:
        return ''
    import json as _json
    from core.checks import validate_check
    try:
        clean = validate_check(raw)
    except PlanError:
        raise
    except Exception as exc:  # RecipeError/CheckError both carry a code
        code = getattr(exc, 'code', 'invalid_check')
        raise PlanError(code, getattr(exc, 'message', str(exc)))
    return _json.dumps(clean, sort_keys=True, separators=(',', ':'))


def _row_to_plan(row, items):
    plan = dict(row)
    out = []
    for item in items:
        entry = dict(item)
        # AV1: the check token is never exposed — not in a plan view, not to
        # the agent, not in History.
        entry.pop('check_token', None)
        raw = entry.pop('check_json', '') or ''
        try:
            entry['check'] = json.loads(raw) if raw else None
        except ValueError:
            entry['check'] = None
        out.append(entry)
    plan['items'] = out
    return plan


def _items_for(conn, plan_id):
    return conn.execute(
        'SELECT * FROM session_plan_items WHERE plan_id = ? ORDER BY id',
        (plan_id,)).fetchall()


def create_plan(session_id: str, items, note: str = '', origin: str = 'agent',
                internal=None, parent=None) -> dict:
    """Store a new plan. Every origin but `check` starts `proposed`.

    F22/AN2: `origin='check'` is the one-item plan a check borrows; it is
    created already approved, from the parent plan's decision, and only
    `core/checks.py` (which holds `CHECK_INTERNAL`) may create one.
    """
    sid = (session_id or '').strip()[:64]
    if not sid:
        raise PlanError('invalid_session', 'session_id is required')
    origin = origin if origin in ('agent', 'probe', 'recipe', 'check') else 'agent'
    if origin == 'check':
        if internal is not CHECK_INTERNAL:
            raise PlanError('invalid_origin',
                            'a check plan may only be created by check code')
        if parent is None:
            raise PlanError('invalid_origin', 'a check plan needs its parent')
        if isinstance(items, (list, tuple)) and len(items) != 1:
            raise PlanError('check_items',
                            'a check plan holds exactly one item')
    normalised = validate_items(items)
    if origin == 'check' and normalised and normalised[0].get('check_json'):
        raise PlanError('check_items', 'a check plan item carries no check')
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
    if origin == 'check':
        # AN2: never approved by a human, never outlives the plan it borrows.
        parent_until = int(parent.get('until') or 0)
        until = min(now + CHECK_TTL_SECONDS, parent_until) if parent_until \
            else now + CHECK_TTL_SECONDS
        status = 'approved'
        decided_at = int(parent.get('decided_at') or 0)
        decided_by = (parent.get('decided_by') or '')[:64]
        parent_plan_id = int(parent.get('id') or 0)
    else:
        status, decided_at, decided_by, parent_plan_id, until = \
            'proposed', 0, '', 0, 0
    with db_conn() as conn:
        cur = conn.cursor()
        # One proposal per session: the previous proposal becomes
        # `superseded` (D7), keeping its row and items, so a poll of the old
        # id can report what replaced it instead of "not found".
        # AN2 (F22): a check plan never replaces a proposal.
        old = []
        if origin != 'check':
            old = [row['id'] for row in cur.execute(
                "SELECT id FROM session_plans WHERE session_id = ? AND status = 'proposed'",
                (sid,)).fetchall()]
            superseded = []
            for old_id in old:
                # BM1: the update repeats the status it read, so a plan the
                # operator approved in the meantime is not turned back into
                # `superseded` (and then linked as replaced).
                cur.execute(
                    "UPDATE session_plans SET status = 'superseded' "
                    "WHERE id = ? AND status = 'proposed'", (old_id,))
                if cur.rowcount == 1:
                    superseded.append(old_id)
            old = superseded
        cur.execute('''
            INSERT INTO session_plans (session_id, status, created_at, note,
                                       origin, decided_at, decided_by, until,
                                       parent_plan_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (sid, status, now, note, origin, decided_at, decided_by, until,
              parent_plan_id))
        plan_id = cur.lastrowid
        for old_id in old:
            cur.execute(
                "UPDATE session_plans SET superseded_by = ? WHERE id = ?",
                (plan_id, old_id))
        first_item_id = 0
        for item in normalised:
            cur.execute('''
                INSERT INTO session_plan_items
                    (plan_id, host, command, max_uses, uses, removed,
                     kind, integration, tool, args_hash, args_json, summary,
                     check_json)
                VALUES (?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?, ?)
            ''', (plan_id, item['host'], item['command'], item['max_uses'],
                    item.get('kind') or 'ssh', item.get('integration') or '',
                    item.get('tool') or '', item.get('args_hash') or '',
                    item.get('args_json') or '', item.get('summary') or '',
                    item.get('check_json') or ''))
            if not first_item_id:
                first_item_id = cur.lastrowid
        conn.commit()
    hosts = {item['host'] for item in normalised}
    return {'plan_id': plan_id, 'status': status, 'session_id': sid,
            'items': len(normalised), 'hosts': len(hosts),
            'item_id': first_item_id}


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
        if cur.rowcount != 1:
            # BM1: another writer moved it (a new proposal superseded it, or a
            # decision landed) between the read and this update.
            conn.rollback()
            raise PlanError('bad_state',
                            'the plan changed while it was being approved')
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
            WHERE id = ? AND status = ?
        ''', (status, now, (decided_by or 'operator')[:64], plan_id,
              plan['status']))
        if cur.rowcount != 1:
            # BM1: the status changed between the read and the write.
            conn.rollback()
            raise PlanError('bad_state',
                            'the plan changed while it was being decided')
        # AN2: a check plan ends with the plan whose approval it borrowed, in
        # the same transaction, so a dispatched check claims nothing.
        _end_child_plans(cur, plan_id, now)
        conn.commit()
    return get_plan(plan_id)


def stop_plan(plan_id: int, reason: str, now: int = None) -> dict:
    """F22 §8: a failed or not-run check ends the plan's authority.

    The status is `stopped` (a new value; `_claim_candidate` only matches
    `approved`), and any check plan that borrowed this plan's approval ends
    with it in the same transaction (AN2)."""
    plan_id = int(plan_id)
    now = int(now or time.time())
    with db_conn() as conn:
        cur = conn.cursor()
        plan = cur.execute('SELECT * FROM session_plans WHERE id = ?',
                           (plan_id,)).fetchone()
        if not plan:
            raise PlanError('not_found', 'plan not found')
        cur.execute('''
            UPDATE session_plans
            SET status = 'stopped', stopped_at = ?, stopped_reason = ?
            WHERE id = ? AND status = 'approved'
        ''', (now, (reason or '')[:300], plan_id))
        _end_child_plans(cur, plan_id, now)
        conn.commit()
    return get_plan(plan_id)


def _end_child_plans(cur, plan_id: int, now: int):
    """AN2: a plan's check plans never outlive the authority they borrowed."""
    cur.execute('''
        UPDATE session_plans SET status = 'ended', decided_at = ?
        WHERE parent_plan_id = ? AND status IN ('proposed', 'approved')
    ''', (now, int(plan_id)))


def link_check_card(session_id: str, target_ip: str, command: str,
                    request_id: int, now: int = None):
    """F22/AR2: tie an approval-mode card to the item whose check follows it.

    Server-side only: the match is the session, one exact host, the whole
    command, an item that carries a check and has no card yet. A `*` item
    never matches — a check needs one exact host. Returns the item id, or
    None (no link, no check).
    """
    sid = (session_id or '').strip()[:64]
    if not sid:
        return None
    now = int(now or time.time())
    host = (target_ip or '').strip()
    if not host or host == '*':
        return None
    with db_conn() as conn:
        cur = conn.cursor()
        row = cur.execute('''
            SELECT i.id
            FROM session_plan_items i
            JOIN session_plans p ON p.id = i.plan_id
            WHERE p.session_id = ? AND p.status = 'approved' AND p.until > ?
              AND p.origin != 'check'
              AND i.removed = 0 AND i.kind = 'ssh'
              AND i.host = ? AND i.command = ?
              AND i.check_json != '' AND i.check_request_id = 0
            ORDER BY i.id LIMIT 1
        ''', (sid, now, host, command or '')).fetchone()
        if not row:
            return None
        cur.execute('UPDATE session_plan_items SET check_request_id = ? '
                    'WHERE id = ? AND check_request_id = 0',
                    (int(request_id), row['id']))
        if cur.rowcount != 1:
            conn.rollback()
            return None
        conn.commit()
        return row['id']


def set_check_pending(item_id: int, token: str, now: int = None) -> bool:
    """F22 §9: write `pending` (with the attempt token) before dispatching.

    Conditional on the item having no state yet, so a duplicate F5 post (or a
    second result) cannot dispatch the check twice."""
    now = int(now or time.time())
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute('''
            UPDATE session_plan_items
            SET check_state = 'pending', check_token = ?, check_at = ?
            WHERE id = ? AND check_state = '' AND check_json != ''
        ''', ((token or '')[:128], now, int(item_id)))
        conn.commit()
        return cur.rowcount == 1


def set_check_running(item_id: int, token: str, plan_id: int,
                      check_plan_id: int = 0, check_item_id: int = 0,
                      now: int = None) -> bool:
    """Move `pending` to `running`, bound to the token the worker read."""
    now = int(now or time.time())
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute('''
            UPDATE session_plan_items
            SET check_state = 'running', check_at = ?,
                check_plan_id = ?, check_item_id = ?
            WHERE id = ? AND check_state = 'pending' AND check_token = ?
        ''', (now, int(check_plan_id or 0), int(check_item_id or 0),
              int(item_id), (token or '')[:128]))
        conn.commit()
        return cur.rowcount == 1


def bump_check_attempt(item_id: int, token: str, limit: int) -> bool:
    """AN5: `check_attempts <= retries + 1`, in every mode."""
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute('''
            UPDATE session_plan_items
            SET check_attempts = check_attempts + 1
            WHERE id = ? AND check_token = ? AND check_attempts < ?
        ''', (int(item_id), (token or '')[:128], int(limit)))
        conn.commit()
        return cur.rowcount == 1


def finish_check_unclaimed(item_id: int, state: str, reason: str = '') -> bool:
    """A check that ends before an attempt (a limiter stop, an item failure).

    Written only while the item has no attempt token."""
    if state not in ('not_run', 'skipped'):
        raise PlanError('bad_state', 'only not_run/skipped end without a token')
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute('''
            UPDATE session_plan_items
            SET check_state = ?, check_reason = ?
            WHERE id = ? AND check_state = '' AND check_json != ''
        ''', (state, (reason or '')[:80], int(item_id)))
        conn.commit()
        return cur.rowcount == 1


def finish_check(item_id: int, token: str, state: str, reason: str = '',
                 exit_code=None, observed=None, expected=None) -> bool:
    """Write the final state once, bound to the attempt token.

    A different token means the attempt belongs to a previous process and is
    abandoned, not honoured (§9, guard 2)."""
    if state not in ('passed', 'failed', 'not_run', 'skipped'):
        raise PlanError('bad_state', 'a check ends passed/failed/not_run/skipped')
    args = [state, (reason or '')[:80],
            -1 if exit_code is None else int(exit_code),
            _cap(observed), _cap(expected), int(item_id), (token or '')[:128]]
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute('''
            UPDATE session_plan_items
            SET check_state = ?, check_reason = ?, check_exit_code = ?,
                check_observed = ?, check_expected = ?
            WHERE id = ? AND check_token = ?
              AND check_state IN ('pending', 'running')
        ''', tuple(args))
        conn.commit()
        return cur.rowcount == 1


def _cap(value):
    if value is None:
        return ''
    import json as _json
    try:
        text = _json.dumps(value, sort_keys=True, separators=(',', ':'))
    except (TypeError, ValueError):
        text = str(value)
    return text[:200]


def interrupted_checks(older_than: int, now: int = None):
    """F22 §9: items left pending/running by a crash, older than the worst
    case. They become not_run; they are never re-dispatched."""
    now = int(now or time.time())
    with db_conn() as conn:
        rows = conn.execute('''
            SELECT i.*, p.status AS plan_status
            FROM session_plan_items i
            JOIN session_plans p ON p.id = i.plan_id
            WHERE i.check_json != ''
              AND i.check_state IN ('pending', 'running')
              AND i.check_at <= ?
        ''', (now - int(older_than),)).fetchall()
        return [dict(row) for row in rows]


def get_plan_item(item_id: int):
    with db_conn() as conn:
        row = conn.execute('SELECT * FROM session_plan_items WHERE id = ?',
                           (int(item_id),)).fetchone()
        return dict(row) if row else None


def set_check_skipped(item_id: int) -> bool:
    """The item's own command failed: the check never runs."""
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute('''
            UPDATE session_plan_items
            SET check_state = 'skipped', check_reason = 'item_failed'
            WHERE id = ? AND check_state = '' AND check_json != ''
        ''', (int(item_id),))
        conn.commit()
        return cur.rowcount == 1


def rotate_check_token(item_id: int, old_token: str, new_token: str) -> bool:
    """AV1: a new random token for every attempt, conditional on the old one."""
    if not _token_ok(new_token):
        return False
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute('''
            UPDATE session_plan_items SET check_token = ?
            WHERE id = ? AND check_state = 'running' AND check_token = ?
        ''', (new_token, int(item_id), (old_token or '')[:128]))
        conn.commit()
        return cur.rowcount == 1


def _token_ok(token):
    import re as _re
    return bool(_re.fullmatch(r'[A-Za-z0-9_-]{16,128}', token or ''))


def set_check_item_token(plan_id: int, token: str) -> bool:
    """The check plan's single item carries the attempt's token (AR6)."""
    if not _token_ok(token):
        return False
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute('UPDATE session_plan_items SET check_token = ? '
                    'WHERE plan_id = ? AND removed = 0',
                    (token, int(plan_id)))
        conn.commit()
        return cur.rowcount == 1


def set_check_item_parent(plan_id: int, parent_item_id: int) -> bool:
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute('UPDATE session_plan_items SET parent_item_id = ? '
                    'WHERE plan_id = ? AND removed = 0',
                    (int(parent_item_id), int(plan_id)))
        conn.commit()
        return cur.rowcount == 1


def release_check_link(plan_item_id: int) -> bool:
    """AV2: a denied or expired card gives its item's link back.

    The item never ran, so a re-sent request for the same bytes must carry its
    check again rather than run without one."""
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute('''
            UPDATE session_plan_items SET check_request_id = 0
            WHERE id = ? AND check_state = ''
        ''', (int(plan_item_id or 0),))
        conn.commit()
        return cur.rowcount == 1


def set_check_plan_refs(item_id: int, plan_id: int, check_item_id: int) -> bool:
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute('UPDATE session_plan_items SET check_plan_id = ?, '
                    'check_item_id = ? WHERE id = ?',
                    (int(plan_id), int(check_item_id), int(item_id)))
        conn.commit()
        return cur.rowcount == 1


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
          AND p.origin != 'check'
          AND i.removed = 0 AND i.uses < i.max_uses
          AND i.kind = 'ssh'
          AND i.command = ? AND (i.host = ? OR i.host = '*')
    '''
    # F22/AN1: a later item (and an item's own next use) waits while an
    # earlier item of the same plan still has an open check. `b.id <= i.id`
    # is deliberate: a two-use write cannot run again while its own check is
    # open, and `uses > 0` means "the command was claimed".
    sql += '''
          AND NOT EXISTS (
            SELECT 1 FROM session_plan_items b
            WHERE b.plan_id = i.plan_id AND b.removed = 0 AND b.id <= i.id
              AND b.check_json != '' AND b.uses > 0
              AND (b.check_state = '' OR b.check_state IN ('pending', 'running'))
          )
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


def _claim_check_candidate(conn, session_id, host, command, check_token,
                           owner, now):
    """F22/AR6: the one-item check plan's claim, bound to the attempt token.

    A check plan is exactly one item on one host with the exact command; a
    second item or a wildcard host is a refusal, not a claim (AR3)."""
    if not check_token:
        return None
    return conn.execute('''
        SELECT i.id, i.plan_id, i.parent_item_id
        FROM session_plan_items i
        JOIN session_plans p ON p.id = i.plan_id
        WHERE p.session_id = ? AND p.status = 'approved' AND p.until > ?
          AND p.origin = 'check' AND p.parent_plan_id > 0
          AND i.removed = 0 AND i.uses < i.max_uses
          AND i.kind = 'ssh'
          AND i.command = ? AND i.host = ?
          AND i.check_token != '' AND i.check_token = ?
          AND EXISTS (SELECT 1 FROM sessions s
                      WHERE s.id = p.session_id AND s.agent != ''
                        AND s.agent = ?)
          AND (SELECT COUNT(*) FROM session_plan_items x
               WHERE x.plan_id = i.plan_id AND x.removed = 0) = 1
        ORDER BY i.id LIMIT 1
    ''', (session_id, now, command, host, check_token, owner)).fetchone()


def claim_check_item(session_id: str, host: str, command: str,
                     check_token: str, owner: str = '', now: int = None):
    """F22: consume one use of the check plan's single item.

    Only `core/checks.py` builds a check plan; this is the one claim that
    matches it. The token is the attempt's (per-attempt, AV1), so the use is
    spent by the attempt the worker started and by nothing else.
    """
    sid = (session_id or '').strip()[:64]
    token = (check_token or '').strip()
    owner = (owner or '').strip()[:64]
    if not sid or not token or not owner:
        return None
    now = int(now or time.time())
    expire_plans(now)
    with db_conn() as conn:
        cur = conn.cursor()
        row = _claim_check_candidate(conn, sid, (host or '').strip(),
                                     command or '', token, owner, now)
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
            conn.rollback()
            return None
        conn.commit()
        return (row['plan_id'], row['id'], int(row['parent_item_id'] or 0))


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

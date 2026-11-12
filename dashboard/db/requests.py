import time
from db.core import db_conn

def init_requests_tables(cursor):
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_ip TEXT NOT NULL,
            command TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        )
    ''')
    # Migrations: add optional columns over time (session_id groups SSH requests into sessions)
    existing = {row[1] for row in cursor.execute("PRAGMA table_info(requests)").fetchall()}
    for col, defval in [('reason', "''"), ('session_id', "''"), ('execution_id', "''")]:
        if col in existing:
            continue
        try:
            cursor.execute(f"ALTER TABLE requests ADD COLUMN {col} TEXT DEFAULT {defval}")
        except Exception:
            pass
    # F7: the real target of a pct/qm/docker wrapper. Deliberately nullable:
    # NULL marks a row that predates the column and still needs the startup
    # backfill, while '' means "this command has no inner target".
    if 'inner_target' not in existing:
        try:
            cursor.execute("ALTER TABLE requests ADD COLUMN inner_target TEXT")
        except Exception:
            pass
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_requests_inner_target "
        "ON requests(target_ip, inner_target)")
    # F18: fingerprints for "seen before" (exact bytes) and "like this"
    # (verb/kind shape). Nullable so the startup backfill can find old rows.
    for col in ('command_sha', 'shape_sha'):
        if col not in existing:
            try:
                cursor.execute(f"ALTER TABLE requests ADD COLUMN {col} TEXT")
            except Exception:
                pass
    # F6: when the row stopped waiting (approved, denied or expired). Nullable
    # so pre-F6 rows stay unknown rather than being given a made-up wait.
    if 'decided_at' not in existing:
        try:
            cursor.execute("ALTER TABLE requests ADD COLUMN decided_at INTEGER")
        except Exception:
            pass
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_requests_command_sha "
                   "ON requests(command_sha)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_requests_shape_sha "
                   "ON requests(shape_sha)")
    # F8: test traffic is kept (the audit trail and History still show it) but
    # left out of statistics, learning gaps and anomaly profiles. 0 for every
    # existing row: nothing is excluded until the operator says so.
    if 'excluded' not in existing:
        try:
            cursor.execute("ALTER TABLE requests ADD COLUMN excluded INTEGER DEFAULT 0")
        except Exception:
            pass
    # F12: the MCP agent that asked for this command ('' = direct SSH).
    if 'agent' not in existing:
        try:
            cursor.execute("ALTER TABLE requests ADD COLUMN agent TEXT DEFAULT ''")
        except Exception:
            pass
    # F19: 1 when the dashboard approved this row without a human deciding on
    # this exact command. Such a row carries a run_limit and is never handed to
    # the poller; it can only be claimed through /api/ticket/{id}, which sends
    # the limit. Existing rows are human-approved, so 0.
    if 'unattended' not in existing:
        try:
            cursor.execute(
                "ALTER TABLE requests ADD COLUMN unattended INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass
    # F19: the JSON limit this row runs under ({seconds, bytes}); '' when the
    # row is not unattended.
    if 'run_limit' not in existing:
        try:
            cursor.execute(
                "ALTER TABLE requests ADD COLUMN run_limit TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass

TICKET_FORMAT_V2 = 'v2'


def gateway_ticket_format(ip: str) -> str:
    """The lockbox format this gateway reported at registration ('' = old)."""
    from db.gateways import get_gateway_ssh_capabilities
    return (get_gateway_ssh_capabilities(ip).get('ssh_ticket_format') or '').strip()


def ticket_line(row, ticket_format: str = '', ts: int = None) -> str:
    """F12 G5: the lockbox line, in the format this gateway understands (M1).

    A v2 line is `v2|ts|agent|session|cmd`: `v2` first so the format is never
    guessed from the number of `|`, and the command last so a `|` inside it
    survives. An old gateway splits on the first `|` and expects `ts|cmd`, so
    it gets exactly that; handing it a v2 line would make every approved
    command fail with "does not match".

    BI3: `ts` is the time the ticket is delivered, not the request's
    `created_at`. The gateway honours the stamp for 90 seconds, so a card
    approved late in its life used to be marked consumed with a line that had
    already expired; the command could not run. Default to `created_at` only
    for the tests and callers that ask for the old behaviour explicitly.
    """
    agent = (row['agent'] if 'agent' in row.keys() else '') or ''
    session = (row['session_id'] if 'session_id' in row.keys() else '') or ''
    stamp = int(ts) if ts is not None else row['created_at']
    if (ticket_format or '').strip() == TICKET_FORMAT_V2:
        return f"v2|{stamp}|{agent}|{session}|{row['command']}"
    return f"{stamp}|{row['command']}"


def _has_line_break(command) -> bool:
    """BI1: a lockbox line is line-based, so a command with CR or LF in it
    would become several lines — the later ones unsigned, unbound and (with a
    future stamp inside the command) never expiring."""
    return '\n' in (command or '') or '\r' in (command or '')


def _refuse_line_break_ticket(req_id, target_ip: str) -> None:
    """BI1: never deliver a line-breaking command through the lockbox.

    The row is denied (terminal, like M1's agent-on-old-format refusal) and
    the audit log says why, instead of an approval that can never run and a
    History row that says "consumed" for it.
    """
    try:
        from db.audit import record_audit_event
        record_audit_event(
            'jit_refused', target_ip,
            details=(f"JIT #{req_id} not delivered: the command contains a "
                     f"line break and cannot be one lockbox line"))
    except Exception:
        pass


def _refuse_agent_ticket(req_id, target_ip: str) -> None:
    """M1: an agent row is never issued in the old format.

    The old `ts|cmd` line has no agent field, so the gateway's `ticket_allowed`
    would treat it as agent-less and an unsigned direct caller could claim the
    agent's approved command. Deny the row instead: the agent's `wait` and
    `check_request` then see a terminal state rather than a 90-second timeout,
    and the audit log records why it never ran.
    """
    try:
        from db.audit import record_audit_event
        record_audit_event(
            'jit_refused', target_ip,
            details=(f"JIT #{req_id} not delivered: agent ticket on a "
                     f"gateway that has not reported {TICKET_FORMAT_V2}"))
    except Exception:
        pass


def create_request(target_ip: str, command: str, status: str = 'pending', ttl: int = 90, reason: str = '', session_id: str = '', execution_id: str = '', inner_target: str = '', agent: str = '', unattended: bool = False, run_limit: str = ''):
    # F18: compute both fingerprints for every new row (no extra call site).
    try:
        from core.fingerprint import fingerprints
        cmd_sha, shape_sha = fingerprints(command)
    except Exception:
        cmd_sha, shape_sha = None, None
    with db_conn() as conn:
        cursor = conn.cursor()
        created_at = int(time.time())
        expires_at = created_at + ttl
        # F6 R1: a row created already decided (allowlist, override, plan,
        # read lane, window, fleet) was decided *at creation* — no human was
        # waited on. Without this the expiry sweep would later stamp
        # `decided_at = now` on an unclaimed row and invent the whole TTL as
        # waiting time.
        decided_at = created_at if status != 'pending' else None
        cursor.execute('''
            INSERT INTO requests (target_ip, command, status, created_at, expires_at, reason, session_id, execution_id, inner_target, command_sha, shape_sha, decided_at, agent, unattended, run_limit)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (target_ip, command, status, created_at, expires_at, reason, session_id, execution_id, inner_target or '', cmd_sha, shape_sha, decided_at, (agent or '')[:64], 1 if unattended else 0, (run_limit or '')[:2000]))
        conn.commit()
        return cursor.lastrowid

def mark_requests_excluded(ids=None, session_id: str = '') -> int:
    """F8: flag rows as test traffic. Idempotent; returns newly flagged rows.

    The rows stay in History (and the audit trail) — only the aggregate views
    stop counting them.
    """
    ids = [int(i) for i in (ids or []) if str(i).strip().isdigit()]
    sid = (session_id or '')[:64]
    if not ids and not sid:
        return 0
    count = 0
    with db_conn() as conn:
        cursor = conn.cursor()
        if ids:
            placeholders = ','.join('?' * len(ids))
            cursor.execute(
                f"UPDATE requests SET excluded = 1 WHERE id IN ({placeholders}) "
                "AND COALESCE(excluded, 0) = 0", ids)
            count += cursor.rowcount
        if sid:
            cursor.execute(
                "UPDATE requests SET excluded = 1 WHERE session_id = ? "
                "AND COALESCE(excluded, 0) = 0", (sid,))
            count += cursor.rowcount
        conn.commit()
    return count


def update_request_status(req_id: int, status: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        if status == 'pending':
            cursor.execute(
                'UPDATE requests SET status = ?, decided_at = NULL WHERE id = ?',
                (status, req_id))
        else:
            # F6: the first decision wins. A later consume/expire pass must not
            # rewrite when the human actually answered.
            cursor.execute(
                'UPDATE requests SET status = ?, decided_at = COALESCE(decided_at, ?) '
                'WHERE id = ?', (status, int(time.time()), req_id))
        conn.commit()


def update_request_status_if_pending(req_id: int, status: str) -> bool:
    """X1: approve/deny may only move a `pending` row.

    The UPDATE is conditional so two racing presses cannot both "win", and an
    old id (denied, expired, consumed, blocked) can never be re-approved by a
    stale click or a colliding integration-call id.
    """
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE requests SET status = ?, decided_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (status, int(time.time()), req_id))
        conn.commit()
        return cursor.rowcount == 1

def update_ticket_consumed_by_ip(target_ip: str):
    fmt = gateway_ticket_format(target_ip)
    ticket = None
    refused = None
    line_break = None
    with db_conn() as conn:
        cursor = conn.cursor()
        now = int(time.time())
        # BI3: no 60 s grace. A row past `expires_at` may not be delivered, so
        # it is never marked consumed for a command the gateway would drop.
        cursor.execute('''
            SELECT id, created_at, expires_at, command, agent, session_id
            FROM requests
            WHERE target_ip = ? AND status = 'approved' AND expires_at > ?
              AND unattended = 0
            ORDER BY id ASC
            LIMIT 1
        ''', (target_ip, now))
        row = cursor.fetchone()
        if row:
            req_id = row['id']
            if (row['agent'] or '') and fmt != TICKET_FORMAT_V2:
                # M1: fail closed. An agent ticket in the old format would be
                # claimable by an unsigned caller; deny it instead.
                cursor.execute(
                    "UPDATE requests SET status = 'denied', decided_at = ? "
                    "WHERE id = ? AND status = 'approved'", (now, req_id))
                if cursor.rowcount == 1:
                    refused = req_id
                conn.commit()
            elif _has_line_break(row['command']):
                # BI1: deny, with an audit reason, rather than issue several
                # lines. The condition keeps the one-winner rule too.
                cursor.execute(
                    "UPDATE requests SET status = 'denied', decided_at = ? "
                    "WHERE id = ? AND status = 'approved'", (now, req_id))
                if cursor.rowcount == 1:
                    line_break = req_id
                conn.commit()
            else:
                # BI2: one approval, one run. Two pollers (or a poll and a
                # by-id claim) both selected this row; only the process whose
                # conditional UPDATE changes a row may deliver it.
                cursor.execute(
                    "UPDATE requests SET status = 'consumed', expires_at = ? "
                    "WHERE id = ? AND status = 'approved'", (now, req_id))
                won = cursor.rowcount == 1
                conn.commit()
                if won:
                    ticket = ticket_line(row, fmt, now)
    if refused is not None:
        _refuse_agent_ticket(refused, target_ip)
    if line_break is not None:
        _refuse_line_break_ticket(line_break, target_ip)
    return ticket

def get_all_requests(session_id: str = None, inner_target: str = None):
    """Newest 200 requests, optionally narrowed to one session or inner target.

    A session filter has to run in SQL, not on the returned page: a session's
    oldest commands are usually outside the newest 200 rows. `inner_target`
    (F7) is an exact match on the recorded container/VM target.
    """
    with db_conn() as conn:
        cursor = conn.cursor()
        base = '''
            SELECT r.*, g.hostname, x.exit_code, x.duration_ms,
                   x.truncated AS output_truncated
            FROM requests r
            LEFT JOIN gateways g ON r.target_ip = g.ip
            LEFT JOIN request_results x ON x.request_id = r.id
            WHERE r.status NOT IN ('integration-approved', 'integration-denied')
        '''
        params = []
        if session_id:
            base += ' AND r.session_id = ?'
            params.append(session_id)
        if inner_target:
            base += ' AND r.inner_target = ?'
            params.append(inner_target)
        cursor.execute(base + ' ORDER BY r.id DESC LIMIT 200', tuple(params))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

def requests_for_sessions(session_ids, limit: int = 20):
    """X4: the newest rows for a set of sessions, one query (agent activity)."""
    ids = [str(s)[:64] for s in (session_ids or []) if s]
    if not ids:
        return []
    limit = max(1, min(int(limit or 20), 100))
    ph = ','.join('?' * len(ids))
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT r.*, g.hostname FROM requests r '
            'LEFT JOIN gateways g ON r.target_ip = g.ip '
            'WHERE r.session_id IN (' + ph + ') '
            'AND r.status NOT IN (\'integration-approved\', \'integration-denied\') '
            'ORDER BY r.created_at DESC, r.id DESC LIMIT ?',
            list(ids) + [limit])
        return [dict(row) for row in cursor.fetchall()]

def find_live_request(target_ip: str, command: str, session_id: str = '',
                      agent: str = ''):
    """D6/BK5: an identical request that is approved and still being delivered.

    Only a signed caller reuses one: `ticket_allowed` binds the claim to that
    agent and session, so returning the id hands the operator's approval back
    to the caller it was granted to. An unsigned caller (`agent=''`, and
    usually no session) matches every other unsigned caller of the same bytes
    on that host; reuse would let someone else ride on an approval the
    operator gave to a different person. Unsigned callers always get a fresh
    card (BI2 still keeps it to one run).
    """
    sid = (session_id or '')[:64]
    who = (agent or '')[:64]
    if not sid or not who:
        return None
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT * FROM requests WHERE target_ip = ? AND command = ? '
            "AND session_id = ? AND agent = ? AND status = 'approved' "
            'AND expires_at > ? ORDER BY id DESC LIMIT 1',
            (target_ip, command, sid, who, int(time.time())))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_pending_request_by_cmd(target_ip: str, command: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM requests WHERE target_ip = ? AND command = ? AND status IN ('pending','approved') LIMIT 1
        ''', (target_ip, command))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_request_status(req_id: int):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT status FROM requests WHERE id = ?', (req_id,))
        row = cursor.fetchone()
        return row['status'] if row else None


def get_request_row(req_id: int):
    """Full row for one request (used by the agent-facing check_request tool)."""
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM requests WHERE id = ?', (req_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def expire_stale_requests(now_ts: int = None):
    """Move timed-out JIT rows to terminal states without deleting them.

    BK1: one expiry rule. A row is approved only until `expires_at`, everywhere
    — the by-id claim, the per-IP poll and this sweep all use the same instant,
    with no extra grace. `expired` = never approved within the TTL;
    `abandoned` = approved but not claimed before `expires_at`.

    Returns (expired_ids, abandoned_ids). The rows and their commands stay for
    the audit trail; only the status changes."""
    now = int(now_ts or time.time())
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM requests WHERE status = 'pending' AND expires_at < ?", (now,))
        expired = [row['id'] for row in cursor.fetchall()]
        cursor.execute("UPDATE requests SET status = 'expired', decided_at = ? "
                       "WHERE status = 'pending' AND expires_at < ?", (now, now))
        cursor.execute("SELECT id FROM requests WHERE status = 'approved' AND expires_at < ?", (now,))
        abandoned = [row['id'] for row in cursor.fetchall()]
        cursor.execute("UPDATE requests SET status = 'abandoned', "
                       "decided_at = COALESCE(decided_at, ?) "
                       "WHERE status = 'approved' AND expires_at < ?", (now, now))
        conn.commit()
        return expired, abandoned

def get_request_command(req_id: int):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT command FROM requests WHERE id = ?', (req_id,))
        row = cursor.fetchone()
        return row['command'] if row else None

def get_request_target_ip(req_id: int):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT target_ip FROM requests WHERE id = ?', (req_id,))
        row = cursor.fetchone()
        return row['target_ip'] if row else None

def count_denied(command: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM requests WHERE command = ? AND status = 'denied'", (command,))
        row = cursor.fetchone()
        return row[0] if row else 0

def get_ticket_by_request_id(req_id: int):
    ticket = None
    refused = None
    line_break = None
    target_ip = ''
    with db_conn() as conn:
        cursor = conn.cursor()
        # BI2: an approved row only. A consumed row is spent; the gateway
        # already handles that state through the lockbox claim, and handing
        # the same approval out again is how one approval ran twice.
        # BK1: and only until `expires_at` — the same rule the sweep and the
        # per-IP poll use. An expired approval is never delivered by id.
        now = int(time.time())
        cursor.execute('SELECT command, created_at, status, agent, session_id, target_ip, unattended, run_limit FROM requests WHERE id = ? AND status = "approved" AND expires_at > ?', (req_id, now))
        row = cursor.fetchone()
        if row:
            target_ip = row['target_ip']
            fmt = gateway_ticket_format(target_ip)
            if (row['agent'] or '') and fmt != TICKET_FORMAT_V2:
                refused = req_id
                cursor.execute(
                    "UPDATE requests SET status = 'denied', decided_at = ? "
                    "WHERE id = ? AND status = 'approved'", (now, req_id))
                if cursor.rowcount != 1:
                    refused = None
                conn.commit()
            elif _has_line_break(row['command']):
                line_break = req_id
                cursor.execute(
                    "UPDATE requests SET status = 'denied', decided_at = ? "
                    "WHERE id = ? AND status = 'approved'", (now, req_id))
                if cursor.rowcount != 1:
                    line_break = None
                conn.commit()
            else:
                cursor.execute(
                    "UPDATE requests SET status = 'consumed', expires_at = ? "
                    "WHERE id = ? AND status = 'approved'", (now, req_id))
                if cursor.rowcount == 1:
                    ticket = {"command": row['command'],
                              "ticket": ticket_line(row, fmt, now),
                              "unattended": 1 if row['unattended'] else 0,
                              "run_limit": row['run_limit'] or ''}
                conn.commit()
    if refused is not None:
        _refuse_agent_ticket(refused, target_ip)
    if line_break is not None:
        _refuse_line_break_ticket(line_break, target_ip)
    return ticket

def delete_old_requests(before_ts: int):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM requests WHERE created_at < ?', (before_ts,))
        conn.commit()

def search_requests(query: str, limit: int = 200, session_id: str = None,
                    inner_target: str = None):
    with db_conn() as conn:
        cursor = conn.cursor()
        pattern = f"%{query}%"
        sql = '''
            SELECT r.*, g.hostname, x.exit_code, x.duration_ms,
                   x.truncated AS output_truncated
            FROM requests r
            LEFT JOIN gateways g ON r.target_ip = g.ip
            LEFT JOIN request_results x ON x.request_id = r.id
            WHERE (r.command LIKE ? OR r.target_ip LIKE ? OR r.status LIKE ? OR g.hostname LIKE ?)
              AND r.status NOT IN ('integration-approved', 'integration-denied')
        '''
        params = [pattern, pattern, pattern, pattern]
        if session_id:
            sql += ' AND r.session_id = ?'
            params.append(session_id)
        if inner_target:
            sql += ' AND r.inner_target = ?'
            params.append(inner_target)
        sql += ' ORDER BY r.id DESC LIMIT ?'
        params.append(limit)
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_session_requests(session_id: str, limit: int = 500):
    """All SSH requests for one session (oldest first), with the gateway
    hostname joined. Excludes the legacy API-surfaced statuses. Backs the
    session-detail view."""
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT r.*, g.hostname, x.exit_code, x.duration_ms,
                   x.truncated AS output_truncated
            FROM requests r
            LEFT JOIN gateways g ON r.target_ip = g.ip
            LEFT JOIN request_results x ON x.request_id = r.id
            WHERE r.session_id = ?
              AND r.status NOT IN ('integration-approved', 'integration-denied')
            ORDER BY r.id ASC LIMIT ?
        ''', (session_id, limit))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

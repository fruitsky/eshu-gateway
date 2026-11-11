"""F23: the change journal — what Eshu changed on one host, and that host's
Pulse alerts next to it.

The page answers "what happened here, and when" without pretending to know
why. Three read-only sources are merged into one timeline:

* **SSH requests** in `requests` that ran (`approved`/`consumed`) and that F1
  (`core.cmd_parse.breakdown`) labels with at least one `write` or `unknown`
  step, plus the F5 exit code and the session. The F7 inner target is kept on
  the item, so a `pct exec 101 …` write on a node names the guest it touched.
* **Integration writes** — the row every integration call already writes
  (`integration_calls`), plus the `audit_log` events that record a change
  Eshu made on a host (`AUDIT_CHANGE_EVENTS`). Each is mapped to a host
  through F14's inventory: the call's own `host`/`resolved_ips` first, then
  the integration's configured base URL, checked against the names the
  inventory knows for the host (its IPs, names, FQDNs, and the guests that run
  on it).
* **Pulse alerts** (`list_alerts`, `scope=history`) for the resources the
  inventory maps to the host, read through `read_policy` exactly as
  `core.inventory._call_tool` does. One read per view, cached for 60 s; a
  missing or failing Pulse still shows the changes, with a note.

The tie-in is a **hint, never a cause**: an alert that started within
`TIE_WINDOW` after a change on the same host names the nearest change and the
gap ("+4 min after #1234"), and counts any others. Eshu never says the change
caused the alert.

Operator-only: this is not an MCP tool.
"""
import re
import time
from datetime import datetime, timezone

from core import inventory
from core.cmd_parse import breakdown
from core.secret_scrub import scrub_string
from db.core import db_conn
from db.exec_results import EXECUTABLE_STATUSES
from db.integrations import get_integration, get_integrations, get_tool

MAX_DAYS = 30
DEFAULT_DAYS = 7
TIE_WINDOW = 30 * 60
CMD_CHARS = 120
CMD_FULL_CHARS = 4000
ALERT_LIMIT = 200
ALERT_CACHE_TTL = 60

# The HTTP methods that change something upstream. A `WS` row is a change when
# its tool is not marked read-only in the tool table (F10's actions are WS).
WRITE_METHODS = ('POST', 'PUT', 'PATCH', 'DELETE')

# `audit_log` rows that mean "Eshu changed something on this host". Today that
# is the gateway update (the gateway and poller files were replaced); every
# other audit row is a refusal or a dashboard setting, not a change here. When
# a later chunk starts writing one row per integration write, this is the one
# place to extend.
AUDIT_CHANGE_EVENTS = ('version_updated',)

_IPV4_RE = re.compile(r'^\d{1,3}(?:\.\d{1,3}){3}$')
_URL_HOST_RE = re.compile(r'^[a-z][a-z0-9+.-]*://(?:[^@/]+@)?([^/:?#]+)')

_ALERT_CACHE = {}
_OWNED_CACHE = {}


# ── arguments ───────────────────────────────────────────────────────────

def clamp_days(days):
    """A 1–30 day range; anything unparseable is the 7-day default."""
    try:
        value = int(days)
    except (TypeError, ValueError):
        return DEFAULT_DAYS
    return max(1, min(MAX_DAYS, value))


def _clip(text, limit):
    text = str(text or '')
    return text if len(text) <= limit else text[:limit - 1] + '…'


# ── the host's identity, from F14 ───────────────────────────────────────

def host_identities(ip, items=None):
    """Every name the inventory knows for this host.

    The IP itself, the names/FQDNs of any entity that claims it, and the
    names/VMIDs of the guests that run on it (a guest's `host` column is the
    node name). Used to decide whether an integration write or a Pulse alert
    belongs to this host.
    """
    if items is None:
        from db.inventory import get_inventory_items
        items = get_inventory_items()
    keys = {str(ip or '')}
    for item in items:
        if ip in (item.get('ips') or []):
            keys.add(str(item.get('name') or ''))
            keys.update(str(f) for f in (item.get('fqdns') or []))
    hosts = {k for k in keys if k}
    for item in items:
        if (item.get('kind') or '') != 'guest':
            continue
        owner = str(item.get('host') or '')
        if owner and (owner in hosts or ip in (item.get('ips') or [])):
            keys.add(str(item.get('name') or ''))
            keys.add(str(item.get('vmid') or ''))
    return {k for k in keys if k}


def _key_pattern(key):
    """A key matches on its own, never as part of a longer number or name.

    An IP and a vmid are bounded by digits and dots (`192.0.2.10` does not
    match `192.0.2.100`, `104` does not match `1040`). A **name** is bounded
    by the characters a hostname label can contain — `(?<![A-Za-z0-9-])name
    (?![A-Za-z0-9-])` — so `pve` owns `pve.example.net` (a dot may follow)
    and `pve:pve:104`, but not `pve3` or `pve-backup`, and `web` does not own
    `webhook.example.net`. A plain substring match made every short name own
    everything that started with it, which is how a false "+N min after" tie
    was possible.
    """
    pattern = _OWNED_CACHE.get(key)
    if pattern is not None:
        return pattern
    escaped = re.escape(key)
    if _IPV4_RE.match(key) or key.isdigit():
        pattern = re.compile(r'(?<![0-9.])' + escaped + r'(?![0-9])')
    else:
        pattern = re.compile(r'(?<![A-Za-z0-9-])' + escaped
                             + r'(?![A-Za-z0-9-])')
    _OWNED_CACHE[key] = pattern
    return pattern


def _owned(value, identities):
    """True when a name/address the inventory knows for the host appears in
    `value` (an IP, a name, a vmid, or a URL that holds one)."""
    if not value:
        return False
    text = str(value)
    match = _URL_HOST_RE.match(text)
    if match:
        text = match.group(1)
    for key in identities:
        if len(key) < 2:
            continue
        if _key_pattern(key).search(text):
            return True
    return False


# ── source 1: SSH requests ──────────────────────────────────────────────

_SQL_REQUESTS = """
    SELECT r.id, r.target_ip, r.command, r.status, r.created_at, r.session_id,
           r.inner_target, r.agent, r.reason, r.excluded,
           res.exit_code,
           s.agent AS session_agent, s.goal AS session_goal
    FROM requests r
    LEFT JOIN request_results res ON res.request_id = r.id
    LEFT JOIN sessions s ON s.id = r.session_id
    WHERE r.target_ip = ? AND r.created_at >= ? AND r.created_at <= ?
    ORDER BY r.created_at DESC, r.id DESC
"""


def _is_change_command(command):
    """F1: a command with a write or an unknown step is a change candidate.

    A pure read is not a change, and an unknown step is kept because the parser
    could not prove it was a read — the journal shows what Eshu ran, not what
    it understood.
    """
    summary = (breakdown(command) or {}).get('summary') or {}
    return bool(summary.get('write') or summary.get('unknown'))


def request_changes(ip, since, until, conn=None):
    """The SSH rows that ran on this host in the window and changed it."""
    sql = (ip, int(since), int(until))
    if conn is not None:
        rows = [dict(r) for r in conn.execute(_SQL_REQUESTS, sql).fetchall()]
    else:
        with db_conn() as own:
            rows = [dict(r) for r in own.execute(_SQL_REQUESTS, sql).fetchall()]
    out = []
    for row in rows:
        if row.get('status') not in EXECUTABLE_STATUSES:
            continue
        command = row.get('command') or ''
        if not _is_change_command(command):
            continue
        clean = scrub_string(command)
        out.append({
            'kind': 'ssh',
            'id': 'request:%d' % row['id'],
            'at': int(row['created_at']),
            'request_id': row['id'],
            'label': '#%d' % row['id'],
            'command': _clip(clean, CMD_CHARS),
            'command_full': _clip(clean, CMD_FULL_CHARS),
            'status': row.get('status') or '',
            'exit_code': row.get('exit_code'),
            'inner_target': row.get('inner_target') or '',
            'session_id': row.get('session_id') or '',
            'session_goal': _clip(scrub_string(row.get('session_goal') or ''),
                                  CMD_CHARS),
            'agent': row.get('agent') or row.get('session_agent') or '',
            'reason': _clip(scrub_string(row.get('reason') or ''), CMD_CHARS),
            'excluded': bool(row.get('excluded')),
        })
    out.sort(key=lambda c: (c['at'], c['request_id']), reverse=True)
    return out


# ── source 2: integration writes ────────────────────────────────────────

_SQL_CALLS = """
    SELECT id, integration, tool, agent, method, path, host, resolved_ips,
           status_code, created_at, outcome, reason_code, session_id
    FROM integration_calls
    WHERE created_at >= ? AND created_at <= ?
    ORDER BY created_at DESC, id DESC
"""

_SQL_AUDIT = """
    SELECT id, event_type, timestamp, gateway_ip, hostname, details, actor
    FROM audit_log
    WHERE timestamp >= ? AND timestamp <= ?
    ORDER BY timestamp DESC, id DESC
"""


def _call_is_write(row, cache=None):
    method = str(row.get('method') or '').upper()
    if method in WRITE_METHODS:
        return True
    if method != 'WS':
        return False
    # A WS row (F10's Supervisor actions): the tool's own read_only flag
    # decides. A tool that has since been deleted is not claimed as a change.
    tool_name = row.get('tool') or ''
    integration_name = row.get('integration') or ''
    key = (integration_name, tool_name)
    if cache is not None and key in cache:
        tool = cache[key]
    else:
        tool = get_tool(integration_name, tool_name) if tool_name else None
        if cache is not None:
            cache[key] = tool
    return bool(tool) and not tool.get('read_only')


def _integration_host(row, integrations=None):
    """The host a call landed on: its own columns first, then the
    integration's configured base URL."""
    host = str(row.get('host') or '').strip()
    if host:
        return host
    ips = str(row.get('resolved_ips') or '')
    if ips:
        return ips.split(',')[0].strip()
    name = row.get('integration') or ''
    integration = (integrations or {}).get(name)
    if integration is None:
        integration = get_integration(name)
    return str((integration or {}).get('base_url') or '')


def integration_changes(ip, since, until, identities=None, conn=None):
    """Integration writes that landed on this host in the window.

    The call rows are keyed by integration, not by host, so the host is
    resolved through F14: the row's own host/resolved IP, else the
    integration's base URL, checked against the names the inventory knows.
    """
    if identities is None:
        identities = host_identities(ip)
    sql = (int(since), int(until))
    if conn is not None:
        calls = [dict(r) for r in conn.execute(_SQL_CALLS, sql).fetchall()]
        audits = [dict(r) for r in conn.execute(_SQL_AUDIT, sql).fetchall()]
    else:
        with db_conn() as own:
            calls = [dict(r) for r in own.execute(_SQL_CALLS, sql).fetchall()]
            audits = [dict(r) for r in own.execute(_SQL_AUDIT, sql).fetchall()]
    # One integration lookup and at most one tool lookup per pair, not per
    # row: a busy host can have thousands of calls in a 30-day window.
    # AJ2: the journal needs only `name` and `base_url`, so it never asks
    # for the secret column.
    integrations = {i['name']: i for i in get_integrations()}
    tools = {}
    out = []
    for row in calls:
        if str(row.get('outcome') or '') != 'ok':
            continue
        if not _call_is_write(row, cache=tools):
            continue
        if not _owned(_integration_host(row, integrations), identities):
            continue
        path = scrub_string(str(row.get('path') or ''))
        out.append({
            'kind': 'integration',
            'id': 'call:%d' % row['id'],
            'at': int(row['created_at']),
            'request_id': None,
            'label': '%s.%s' % (row.get('integration') or '?',
                                row.get('tool') or '?'),
            'integration': row.get('integration') or '',
            'tool': row.get('tool') or '',
            'method': str(row.get('method') or '').upper(),
            'command': _clip(path, CMD_CHARS),
            'command_full': _clip(path, CMD_FULL_CHARS),
            'status_code': row.get('status_code'),
            'exit_code': None,
            'inner_target': '',
            'session_id': row.get('session_id') or '',
            'session_goal': '',
            'agent': row.get('agent') or '',
            'reason': _clip(scrub_string(row.get('reason_code') or ''), CMD_CHARS),
            'excluded': False,
        })
    for row in audits:
        if row.get('event_type') not in AUDIT_CHANGE_EVENTS:
            continue
        if not _owned(row.get('gateway_ip') or row.get('hostname'), identities):
            continue
        details = scrub_string(str(row.get('details') or ''))
        out.append({
            'kind': 'audit',
            'id': 'audit:%d' % row['id'],
            'at': int(row['timestamp']),
            'request_id': None,
            'label': row.get('event_type') or 'event',
            'event_type': row.get('event_type') or '',
            'command': _clip(details, CMD_CHARS),
            'command_full': _clip(details, CMD_FULL_CHARS),
            'exit_code': None,
            'inner_target': '',
            'session_id': '',
            'session_goal': '',
            'agent': row.get('actor') or '',
            'reason': '',
            'excluded': False,
        })
    out.sort(key=lambda c: (c['at'], c['id']), reverse=True)
    return out


# ── source 3: Pulse alerts ──────────────────────────────────────────────

def _alert_parts(row):
    """The strings one alert may be matched on.

    A monitoring id is `<cluster>:<node>:<vmid>` (Pulse), and the first
    segment is the cluster or integration prefix — often literally `pve` —
    never the host. It is dropped, so a host named `pve` does not own every
    `pve:*` alert; the node and vmid segments stay, which is what makes
    `pve:pve:104` belong to the host `pve`.
    """
    parts = []
    for key in ('resourceId', 'id'):
        text = str(row.get(key) or '')
        if not text:
            continue
        bits = [b for b in text.split(':') if b]
        if len(bits) >= 3:
            bits = bits[1:]
        parts.extend(bits)
    for key in ('resourceName', 'node'):
        text = str(row.get(key) or '')
        if text:
            parts.append(text)
    return parts



def _alert_start(value):
    """Pulse's `startTime` is ISO-8601 (`2026-08-02T12:07:00.622Z`). Accept an
    epoch too, and return 0 when it cannot be read."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or '').strip()
    if not text:
        return 0
    if text.isdigit():
        return int(text)
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def _pulse_integration():
    found = inventory._integrations('pulse')
    return found[0] if found else None


def pulse_alerts(identities, since, until, now=None):
    """This host's Pulse alerts (history) and an error string when unavailable.

    One read per view: the result is cached for `ALERT_CACHE_TTL` seconds. The
    read goes through `core.inventory._call_tool`, so `read_policy` refuses a
    tool that is not a read, exactly like every other inventory source.
    """
    now = int(now or time.time())
    integration = _pulse_integration()
    if not integration:
        return [], 'alerts unavailable (no Pulse integration)'
    # The upstream list does not depend on the window — each view filters the
    # cached rows itself — so the cache key is the integration, and one read
    # serves every range for `ALERT_CACHE_TTL` seconds.
    cache_key = integration.get('name') or ''
    cached = _ALERT_CACHE.get(cache_key)
    if cached and now - cached['at'] < ALERT_CACHE_TTL:
        rows, error = cached['rows'], cached['error']
    else:
        try:
            data = inventory._call_tool(integration, 'list_alerts',
                                        {'scope': 'history', 'limit': ALERT_LIMIT})
            rows = data if isinstance(data, list) else []
            error = ''
        except inventory.SourceError as exc:
            rows, error = [], 'alerts unavailable (%s)' % exc
        except Exception as exc:  # noqa: BLE001 — a view must not 500 on Pulse
            rows, error = [], 'alerts unavailable (%s)' % type(exc).__name__
        _ALERT_CACHE[cache_key] = {'at': now, 'rows': rows, 'error': error}

    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        start = _alert_start(row.get('startTime'))
        if not start or start < since or start > until:
            continue
        if not any(_owned(part, identities) for part in _alert_parts(row)):
            continue
        out.append({
            'id': str(row.get('id') or ''),
            'at': start,
            'level': str(row.get('level') or ''),
            'type': str(row.get('type') or ''),
            'message': _clip(scrub_string(str(row.get('message') or '')), 300),
            'resource': _clip(str(row.get('resourceName') or
                                  row.get('resourceId') or ''), 120),
            'node': str(row.get('node') or ''),
            'acknowledged': bool(row.get('acknowledged')),
            'tie': None,
        })
    out.sort(key=lambda a: (a['at'], a['id']), reverse=True)
    return out, error


# ── the tie-in ──────────────────────────────────────────────────────────

def attach_ties(changes, alerts, window=TIE_WINDOW):
    """Name the nearest change before each alert, and count the others.

    A hint, not a cause: the gap and the change are shown, nothing is claimed.
    """
    ordered = sorted(changes, key=lambda c: c['at'])
    for alert in alerts:
        start = alert.get('at') or 0
        before = [c for c in ordered
                  if c['at'] <= start and start - c['at'] <= window]
        if not before:
            alert['tie'] = None
            continue
        nearest = before[-1]
        gap = int((start - nearest['at']) // 60)
        alert['tie'] = {
            'change_id': nearest['id'],
            'request_id': nearest.get('request_id'),
            'label': nearest['label'],
            'gap_min': gap,
            'others': len(before) - 1,
            'text': '+%d min after %s' % (gap, nearest['label']),
        }
    return alerts


# ── the view ────────────────────────────────────────────────────────────

def changes_for_host(ip, days=None, now=None):
    """One host's timeline: changes (newest first), alerts, and the counts.

    An unknown host still returns an empty timeline rather than a 404, so a
    removed gateway's history stays readable by IP.
    """
    ip = str(ip or '').strip()
    if not _IPV4_RE.match(ip):
        raise ValueError('bad_host')
    days = clamp_days(days)
    now = int(now or time.time())
    since = now - days * 86400
    identities = host_identities(ip)
    changes = request_changes(ip, since, now)
    changes.extend(integration_changes(ip, since, now, identities=identities))
    changes.sort(key=lambda c: (c['at'], c['id']), reverse=True)
    alerts, error = pulse_alerts(identities, since, now, now=now)
    attach_ties(changes, alerts)
    return {
        'host': ip,
        'days': days,
        'from': since,
        'to': now,
        'changes': changes,
        'alerts': alerts,
        'alerts_error': error,
        'counts': {
            'changes': len(changes),
            'ssh': sum(1 for c in changes if c['kind'] == 'ssh'),
            'integration': sum(1 for c in changes if c['kind'] != 'ssh'),
            'alerts': len(alerts),
            'ties': sum(1 for a in alerts if a.get('tie')),
        },
    }


def reset_alert_cache():
    """Tests use this; the view only ever reads the cache."""
    _ALERT_CACHE.clear()

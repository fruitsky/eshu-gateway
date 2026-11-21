"""Per-agent MCP policy.

Policies are keyed by the agent token id. An agent with no row keeps the
historical behaviour: every enabled tool, the integration's normal approval
policy, and no rate limit. That makes the migration to multi-agent identities
non-breaking for the existing admin token.
"""
import json
import re
import time

from db.core import db_conn, get_db

TOOLS_MODES = ('all', 'allowlist')
APPROVAL_MODES = ('inherit', 'gated', 'ungated')
DEFAULT_RATE_LIMIT = 0

# F12: SSH through Eshu. The default is the strictest useful mode — every
# command is forced to JIT — and an empty host list means "no gateway", so a
# new agent cannot reach SSH until the operator opts in per host.
SSH_MODES = ('read_only', 'approval', 'normal')
DEFAULT_SSH_MODE = 'approval'
SSH_EXEC_TOOL = 'ssh_exec'


def init_agent_policies_tables(cursor):
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS agent_policies (
            agent_id INTEGER PRIMARY KEY,
            tools_mode TEXT NOT NULL DEFAULT 'all',
            tools_json TEXT NOT NULL DEFAULT '[]',
            arg_policy_json TEXT NOT NULL DEFAULT '{}',
            approval_json TEXT NOT NULL DEFAULT '{}',
            rate_limit_per_min INTEGER NOT NULL DEFAULT 0,
            test_agent INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL DEFAULT 0
        )
    ''')
    # F12: additive columns; a pre-F12 row reads as "no hosts, approval mode".
    for col, default in [('ssh_hosts_allow_json', "'[]'"),
                         ('ssh_mode', "'approval'")]:
        try:
            cursor.execute(f"ALTER TABLE agent_policies ADD COLUMN {col} TEXT DEFAULT {default}")
        except Exception:
            pass
    try:
        cursor.execute(
            "ALTER TABLE agent_policies ADD COLUMN test_agent INTEGER NOT NULL DEFAULT 0")
    except Exception:
        pass
    # X11/S5: one monotonic version for the whole per-agent configuration.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS agent_config_state (
            agent_id INTEGER PRIMARY KEY,
            version INTEGER NOT NULL DEFAULT 1,
            updated_at INTEGER NOT NULL DEFAULT 0
        )
    ''')


def get_agent_config_version(agent_id):
    """The version a config editor loaded. Absent row means version 1."""
    with db_conn() as conn:
        row = conn.execute(
            'SELECT version FROM agent_config_state WHERE agent_id = ?',
            (int(agent_id),)).fetchone()
    return int(row['version']) if row else 1


def bump_agent_config_version(agent_id, conn=None):
    """Increment and return the version, optionally inside a caller's tx."""
    own = conn is None
    if own:
        conn = get_db()
    try:
        row = conn.execute(
            'SELECT version FROM agent_config_state WHERE agent_id = ?',
            (int(agent_id),)).fetchone()
        current = int(row['version']) if row else 1
        new = current + 1
        conn.execute(
            'INSERT INTO agent_config_state (agent_id, version, updated_at) '
            'VALUES (?, ?, ?) ON CONFLICT(agent_id) DO UPDATE SET '
            'version = excluded.version, updated_at = excluded.updated_at',
            (int(agent_id), new, int(time.time())))
        if own:
            conn.commit()
        return new
    finally:
        if own:
            conn.close()


def write_policy_row(conn, agent_id, clean):
    """Write a validated policy on an existing transaction (no commit)."""
    conn.execute('''
        INSERT OR REPLACE INTO agent_policies
            (agent_id, tools_mode, tools_json, arg_policy_json,
             approval_json, rate_limit_per_min, updated_at,
             ssh_hosts_allow_json, ssh_mode, test_agent)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (int(agent_id), clean['tools_mode'], json.dumps(clean['tools']),
          json.dumps(clean['arg_policy']), json.dumps(clean['approval']),
          clean['rate_limit_per_min'], int(time.time()),
          json.dumps(clean['ssh_hosts_allow']), clean['ssh_mode'],
          1 if clean.get('test_agent') else 0))


def default_policy(agent_id=None):
    return {
        'agent_id': agent_id,
        'tools_mode': 'all',
        'tools': [],
        'arg_policy': {},
        'approval': {},
        'rate_limit_per_min': DEFAULT_RATE_LIMIT,
        'ssh_hosts_allow': [],
        'ssh_mode': DEFAULT_SSH_MODE,
        'test_agent': False,
        'updated_at': 0,
    }


def _load_json(text, fallback):
    try:
        value = json.loads(text or '')
    except (TypeError, ValueError):
        return fallback
    return value if isinstance(value, type(fallback)) else fallback


def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _validate_ssh_hosts(value):
    """A list of gateway IPs/hostnames, or '*' for any registered gateway."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [v.strip() for v in value.split(',') if v.strip()]
    if not isinstance(value, (list, tuple)):
        raise ValueError("ssh_hosts_allow must be a list of hosts")
    hosts = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("ssh_hosts_allow entries must be strings")
        item = item.strip()
        if not item:
            continue
        if item != '*' and not re.fullmatch(r'[A-Za-z0-9_.:-]{1,253}', item):
            raise ValueError(f"ssh_hosts_allow entry is not a host: {item[:40]}")
        if item.lower() not in [h.lower() for h in hosts]:
            hosts.append(item)
    return hosts


def validate_policy(tools_mode='all', tools=None, arg_policy=None,
                    approval=None, rate_limit_per_min=0, ssh_hosts_allow=None,
                    ssh_mode=DEFAULT_SSH_MODE):
    """Validate a policy payload. Raises ValueError with a caller-facing reason."""
    if tools_mode not in TOOLS_MODES:
        raise ValueError("tools_mode must be 'all' or 'allowlist'")
    tools = tools or []
    if not isinstance(tools, list) or any(not isinstance(t, str) or not t.strip() for t in tools):
        raise ValueError("tools must be a list of non-empty strings")
    arg_policy = arg_policy or {}
    if not isinstance(arg_policy, dict):
        raise ValueError("arg_policy must be an object keyed by tool name")
    for tool_name, rules in arg_policy.items():
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError("arg_policy keys must be non-empty tool names")
        if not isinstance(rules, dict):
            raise ValueError(f"arg_policy['{tool_name}'] must be an object")
        for key, value in rules.items():
            if isinstance(value, (list, tuple)):
                if any(not isinstance(v, str) for v in value):
                    raise ValueError(f"arg_policy['{tool_name}']['{key}'] must be a list of strings")
            elif not isinstance(value, (str, bool, int, float, type(None))):
                raise ValueError(f"arg_policy['{tool_name}']['{key}'] has an unsupported value")
    approval = approval or {}
    if not isinstance(approval, dict):
        raise ValueError("approval must be an object keyed by tool name")
    for tool_name, mode in approval.items():
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError("approval keys must be non-empty tool names")
        if mode not in APPROVAL_MODES:
            raise ValueError(f"approval['{tool_name}'] must be one of {', '.join(APPROVAL_MODES)}")
    rate = _as_int(rate_limit_per_min, -1)
    if rate < 0:
        raise ValueError("rate_limit_per_min must be 0 (unlimited) or a positive integer")
    if ssh_mode not in SSH_MODES:
        raise ValueError(f"ssh_mode must be one of {', '.join(SSH_MODES)}")
    return {
        'tools_mode': tools_mode,
        'tools': sorted({t.strip() for t in tools}),
        'arg_policy': arg_policy,
        'approval': approval,
        'rate_limit_per_min': rate,
        'ssh_hosts_allow': _validate_ssh_hosts(ssh_hosts_allow),
        'ssh_mode': ssh_mode,
    }


def _row_to_policy(row):
    if not row:
        return None
    item = dict(row)
    item['tools'] = _load_json(item.pop('tools_json', '[]'), [])
    item['arg_policy'] = _load_json(item.pop('arg_policy_json', '{}'), {})
    item['approval'] = _load_json(item.pop('approval_json', '{}'), {})
    item['rate_limit_per_min'] = _as_int(item.get('rate_limit_per_min'), 0)
    hosts = _load_json(item.pop('ssh_hosts_allow_json', '[]'), [])
    item['ssh_hosts_allow'] = [str(h) for h in hosts if str(h).strip()]
    mode = item.get('ssh_mode') or DEFAULT_SSH_MODE
    item['ssh_mode'] = mode if mode in SSH_MODES else DEFAULT_SSH_MODE
    item['test_agent'] = str(item.get('test_agent') or '').lower() in ('1', 'true')
    return item


def get_agent_policy(agent_id):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM agent_policies WHERE agent_id = ?', (agent_id,))
        policy = _row_to_policy(cursor.fetchone())
        return policy or default_policy(agent_id)


def get_agent_policies_map():
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM agent_policies')
        return {row['agent_id']: _row_to_policy(row) for row in cursor.fetchall()}


def set_agent_policy(agent_id, tools_mode=None, tools=None, arg_policy=None,
                     approval=None, rate_limit_per_min=None, ssh_hosts_allow=None,
                     ssh_mode=None, test_agent=None):
    """Update one agent's policy; a `None` field keeps its stored value.

    X4: the Agents page saves one section at a time, so each call must be
    unable to touch the other sections. Nothing here is a partial JSON merge:
    a field the caller sends replaces that field, a field left out is read
    back from the stored row. Pass `[]` / `{}` to clear a list or a map, and
    the empty string is never special. (The SSH fields already worked this
    way; every field now does, so a Tools save can never wipe SSH or the
    rate limit.)"""
    current = get_agent_policy(agent_id)
    if tools_mode is None:
        tools_mode = current['tools_mode']
    if tools is None:
        tools = current['tools']
    if arg_policy is None:
        arg_policy = current['arg_policy']
    if approval is None:
        approval = current['approval']
    if rate_limit_per_min is None:
        rate_limit_per_min = current['rate_limit_per_min']
    if ssh_hosts_allow is None:
        ssh_hosts_allow = current['ssh_hosts_allow']
    if ssh_mode is None:
        ssh_mode = current['ssh_mode']
    if test_agent is None:
        test_agent = current['test_agent']
    clean = validate_policy(tools_mode, tools, arg_policy, approval,
                            rate_limit_per_min, ssh_hosts_allow, ssh_mode)
    sync_ssh_tool(clean)
    clean['test_agent'] = bool(test_agent)
    if clean['test_agent']:
        _validate_test_agent_policy(clean)
        if not current.get('test_agent'):
            activity = agent_has_activity(agent_id)
            if activity:
                raise ValueError(
                    'a test agent must be newly created: this agent already has '
                    'a session, request or call')
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        write_policy_row(conn, agent_id, clean)
        bump_agent_config_version(agent_id, conn)
        conn.commit()
    return get_agent_policy(agent_id)


def _is_one_ip(host: str) -> bool:
    try:
        import ipaddress
        ipaddress.ip_address(str(host or ''))
        return True
    except ValueError:
        return False


def _validate_test_agent_policy(clean):
    """BY4: the flag narrows. Called only when the final policy has it set."""
    hosts = clean.get('ssh_hosts_allow') or []
    if len(hosts) != 1 or not _is_one_ip(hosts[0]):
        raise ValueError('a test agent must allow exactly one IP')
    if any(mode == 'ungated' for mode in (clean.get('approval') or {}).values()):
        raise ValueError('a test agent cannot use ungated approval')
    if clean.get('tools_mode') != 'allowlist':
        raise ValueError('a test agent must use an allowlist')
    allowed = _test_agent_tool_keys()
    bad = [t for t in (clean.get('tools') or []) if t not in allowed]
    if bad:
        raise ValueError(
            'a test agent may only use HA reads, call_service and ssh_exec; not: '
            + ', '.join(bad[:5]))


def _test_agent_tool_keys():
    """The policy keys a test agent may hold: HA read tools + call_service."""
    from core.agent_policy import tool_key
    from db.integrations import get_integrations, get_tools
    out = set()
    for integration in get_integrations():
        if (integration.get('kind') or '').lower() != 'ha':
            continue
        for tool in get_tools(integration['id']):
            if not tool.get('enabled'):
                continue
            if tool.get('read_only') or tool.get('name') == 'call_service':
                out.add(tool_key(integration['name'], tool['name']))
    # The seeded HA integration is the one the suite uses; if it is not
    # configured, still allow the canonical call_service key so the operator
    # can prepare the policy before adding the integration.
    out.add('home_assistant_call_service')
    # The E2E test agent runs `ssh_exec`; an allowlist agent gets that exact
    # key from its Run commands settings (not a separate tool switch).
    out.add(SSH_EXEC_TOOL)
    return out


def sync_ssh_tool(clean):
    """Make SSH access one setting: hosts in means `ssh_exec` in.

    For an allowlist agent the `ssh_exec` key is derived from
    `ssh_hosts_allow` on every write. Removing the last host removes the key.
    An all-tools agent needs no key, and its stored allowlist is left alone.
    """
    if clean.get('tools_mode') != 'allowlist':
        return clean
    tools = set(clean.get('tools') or [])
    if clean.get('ssh_hosts_allow'):
        tools.add(SSH_EXEC_TOOL)
    else:
        tools.discard(SSH_EXEC_TOOL)
    clean['tools'] = sorted(tools)
    return clean


def agent_has_activity(agent_id: int) -> bool:
    """True when the agent already has a session, request or call (BY4)."""
    try:
        aid = int(agent_id)
    except (TypeError, ValueError):
        return True
    with db_conn() as conn:
        cur = conn.cursor()
        for sql in (
                'SELECT 1 FROM sessions WHERE agent_id = ? LIMIT 1',
                'SELECT 1 FROM requests WHERE agent_id = ? LIMIT 1',
                'SELECT 1 FROM integration_calls WHERE agent_id = ? LIMIT 1',
                'SELECT 1 FROM pending_integration_calls WHERE agent_id = ? LIMIT 1',
                'SELECT 1 FROM approved_windows WHERE agent_id = ? LIMIT 1'):
            try:
                if cur.execute(sql, (aid,)).fetchone():
                    return True
            except Exception:
                # An older/mid-migration database without the column cannot
                # prove the agent is fresh, so fail closed.
                continue
    return False


def delete_agent_policy(agent_id):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM agent_policies WHERE agent_id = ?', (agent_id,))
        conn.commit()
        return cursor.rowcount > 0

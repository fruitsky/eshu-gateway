"""Per-agent MCP policy.

Policies are keyed by the agent token id. An agent with no row keeps the
historical behaviour: every enabled tool, the integration's normal approval
policy, and no rate limit. That makes the migration to multi-agent identities
non-breaking for the existing admin token.
"""
import json
import time

from db.core import db_conn

TOOLS_MODES = ('all', 'allowlist')
APPROVAL_MODES = ('inherit', 'gated', 'ungated')
DEFAULT_RATE_LIMIT = 0


def init_agent_policies_tables(cursor):
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS agent_policies (
            agent_id INTEGER PRIMARY KEY,
            tools_mode TEXT NOT NULL DEFAULT 'all',
            tools_json TEXT NOT NULL DEFAULT '[]',
            arg_policy_json TEXT NOT NULL DEFAULT '{}',
            approval_json TEXT NOT NULL DEFAULT '{}',
            rate_limit_per_min INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL DEFAULT 0
        )
    ''')


def default_policy(agent_id=None):
    return {
        'agent_id': agent_id,
        'tools_mode': 'all',
        'tools': [],
        'arg_policy': {},
        'approval': {},
        'rate_limit_per_min': DEFAULT_RATE_LIMIT,
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


def validate_policy(tools_mode='all', tools=None, arg_policy=None,
                    approval=None, rate_limit_per_min=0):
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
    return {
        'tools_mode': tools_mode,
        'tools': sorted({t.strip() for t in tools}),
        'arg_policy': arg_policy,
        'approval': approval,
        'rate_limit_per_min': rate,
    }


def _row_to_policy(row):
    if not row:
        return None
    item = dict(row)
    item['tools'] = _load_json(item.pop('tools_json', '[]'), [])
    item['arg_policy'] = _load_json(item.pop('arg_policy_json', '{}'), {})
    item['approval'] = _load_json(item.pop('approval_json', '{}'), {})
    item['rate_limit_per_min'] = _as_int(item.get('rate_limit_per_min'), 0)
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


def set_agent_policy(agent_id, tools_mode='all', tools=None, arg_policy=None,
                     approval=None, rate_limit_per_min=0):
    clean = validate_policy(tools_mode, tools, arg_policy, approval, rate_limit_per_min)
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO agent_policies
                (agent_id, tools_mode, tools_json, arg_policy_json,
                 approval_json, rate_limit_per_min, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (agent_id, clean['tools_mode'], json.dumps(clean['tools']),
              json.dumps(clean['arg_policy']), json.dumps(clean['approval']),
              clean['rate_limit_per_min'], int(time.time())))
        conn.commit()
    return get_agent_policy(agent_id)


def delete_agent_policy(agent_id):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM agent_policies WHERE agent_id = ?', (agent_id,))
        conn.commit()
        return cursor.rowcount > 0

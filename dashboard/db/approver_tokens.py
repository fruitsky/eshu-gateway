"""BY1–BY3: scoped approver tokens for the E2E suite.

A token is minted for one agent id and carries the exact command list and the
one test gateway the suite may use. Only its SHA-256 hash is stored; the raw
token is shown once. The token is a decision credential, never an agent or
gateway credential.
"""
import hashlib
import ipaddress
import json
import secrets
import time

from db.core import db_conn

PREFIX = 'e2e_'
MAX_DAYS = 30
MAX_COMMANDS = 64
MAX_COMMAND_BYTES = 8192


def init_approver_tokens_tables(cursor):
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS approver_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER NOT NULL,
            token_hash TEXT UNIQUE NOT NULL,
            commands_json TEXT NOT NULL DEFAULT '[]',
            gateway_ip TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            revoked INTEGER NOT NULL DEFAULT 0,
            last_used_at INTEGER NOT NULL DEFAULT 0,
            created_by TEXT NOT NULL DEFAULT ''
        )
    ''')
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_approver_tokens_agent '
        'ON approver_tokens(agent_id)')


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()


def _clean_commands(commands):
    if not isinstance(commands, (list, tuple)):
        raise ValueError('commands must be a list')
    out = []
    for value in commands:
        if not isinstance(value, str):
            raise ValueError('every command must be a string')
        value = value.rstrip('\r')
        if not value:
            continue
        if '\n' in value or '\r' in value:
            raise ValueError('a command must be one line')
        if len(value.encode('utf-8')) > MAX_COMMAND_BYTES:
            raise ValueError('a command is too long')
        if value not in out:
            out.append(value)
    if not out:
        raise ValueError('at least one command is required')
    if len(out) > MAX_COMMANDS:
        raise ValueError('too many commands')
    return out


def _clean_gateway(gateway_ip):
    text = str(gateway_ip or '').strip()
    try:
        ipaddress.ip_address(text)
    except ValueError:
        raise ValueError('gateway_ip must be an IP')
    return text


def create_approver_token(agent_id, commands, gateway_ip, days=MAX_DAYS,
                          actor='operator'):
    commands = _clean_commands(commands)
    gateway_ip = _clean_gateway(gateway_ip)
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = MAX_DAYS
    days = max(1, min(MAX_DAYS, days))
    raw = PREFIX + secrets.token_hex(32)
    now = int(time.time())
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO approver_tokens
                (agent_id, token_hash, commands_json, gateway_ip, created_at,
                 expires_at, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (int(agent_id), _hash_token(raw), json.dumps(commands),
              gateway_ip, now, now + days * 86400, (actor or '')[:64]))
        conn.commit()
        return raw, cur.lastrowid


def get_approver_by_token(raw_token: str):
    raw = (raw_token or '').strip()
    if not raw.startswith(PREFIX):
        return None
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute('SELECT * FROM approver_tokens WHERE token_hash = ?',
                    (_hash_token(raw),))
        row = cur.fetchone()
    if not row:
        return None
    item = dict(row)
    now = int(time.time())
    if item.get('revoked') or int(item.get('expires_at') or 0) <= now:
        return None
    try:
        item['commands'] = json.loads(item.get('commands_json') or '[]')
    except (TypeError, ValueError):
        item['commands'] = []
    return item


def get_approver_token(token_id: int):
    with db_conn() as conn:
        row = conn.execute('SELECT * FROM approver_tokens WHERE id = ?',
                           (int(token_id),)).fetchone()
    if not row:
        return None
    item = dict(row)
    try:
        item['commands'] = json.loads(item.get('commands_json') or '[]')
    except (TypeError, ValueError):
        item['commands'] = []
    return item


def list_approver_tokens(agent_id=None):
    with db_conn() as conn:
        cur = conn.cursor()
        if agent_id is None:
            cur.execute('SELECT * FROM approver_tokens ORDER BY id DESC')
        else:
            cur.execute(
                'SELECT * FROM approver_tokens WHERE agent_id = ? ORDER BY id DESC',
                (int(agent_id),))
        rows = [dict(r) for r in cur.fetchall()]
    now = int(time.time())
    out = []
    for item in rows:
        out.append({
            'id': item['id'], 'agent_id': item['agent_id'],
            'commands': json.loads(item.get('commands_json') or '[]'),
            'gateway_ip': item.get('gateway_ip') or '',
            'created_at': item.get('created_at') or 0,
            'expires_at': item.get('expires_at') or 0,
            'revoked': bool(item.get('revoked')),
            'expired': int(item.get('expires_at') or 0) <= now,
            'last_used_at': item.get('last_used_at') or 0,
            'created_by': item.get('created_by') or '',
        })
    return out


def revoke_approver_token(token_id: int) -> bool:
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute('UPDATE approver_tokens SET revoked = 1 WHERE id = ?',
                    (int(token_id),))
        conn.commit()
        return cur.rowcount > 0


def revoke_agent_tokens(agent_id: int) -> int:
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute('UPDATE approver_tokens SET revoked = 1 '
                    'WHERE agent_id = ? AND revoked = 0', (int(agent_id),))
        conn.commit()
        return cur.rowcount


def touch_approver_token(token_id: int):
    with db_conn() as conn:
        conn.execute('UPDATE approver_tokens SET last_used_at = ? WHERE id = ?',
                     (int(time.time()), int(token_id)))
        conn.commit()

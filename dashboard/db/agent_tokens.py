import hashlib
import secrets
import time
from db.core import db_conn


def init_agent_tokens_tables(cursor):
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS agent_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            token_hash TEXT UNIQUE NOT NULL,
            created_at INTEGER NOT NULL,
            last_used_at INTEGER NOT NULL DEFAULT 0,
            revoked INTEGER NOT NULL DEFAULT 0
        )
    ''')


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()


def create_agent_token(name: str):
    """Mint a new agent token. The raw token is returned exactly once; only its
    SHA-256 hash is persisted."""
    raw = secrets.token_hex(32)
    with db_conn() as conn:
        cursor = conn.cursor()
        now = int(time.time())
        cursor.execute('''
            INSERT INTO agent_tokens (name, token_hash, created_at)
            VALUES (?, ?, ?)
        ''', (name, _hash_token(raw), now))
        conn.commit()
        return raw, cursor.lastrowid


def get_agent_tokens():
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id, name, created_at, last_used_at, revoked FROM agent_tokens ORDER BY id ASC')
        return [dict(row) for row in cursor.fetchall()]


def get_agent_by_token(raw_token: str):
    """Resolve a raw bearer token to its agent record (hash lookup)."""
    if not raw_token:
        return None
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM agent_tokens WHERE token_hash = ?', (_hash_token(raw_token),))
        row = cursor.fetchone()
        return dict(row) if row else None


def touch_agent_token(agent_id: int):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE agent_tokens SET last_used_at = ? WHERE id = ?', (int(time.time()), agent_id))
        conn.commit()


def revoke_agent_token(agent_id: int) -> bool:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE agent_tokens SET revoked = 1 WHERE id = ?', (agent_id,))
        conn.commit()
        return cursor.rowcount > 0


def delete_agent_token(agent_id: int) -> bool:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM agent_tokens WHERE id = ?', (agent_id,))
        conn.commit()
        return cursor.rowcount > 0

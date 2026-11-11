"""F21: the snapshot journal.

One row per snapshot Eshu made. The row is the only thing the rollback, the
delete and the expiry job act on: they take a row id, never a vmid or a name
from the browser, and every action re-checks the row against the live guest
before it touches anything (see `core/snapshots.py`).
"""
import time

from db.core import db_conn

# creating → active → rolled_back | deleted | expired | gone
#          ↘ failed ↘ orphaned
STATES = ('creating', 'active', 'rolled_back', 'deleted', 'expired', 'gone',
          'failed', 'orphaned')
LIVE_STATES = ('creating', 'active', 'failed')


def init_snapshots_tables(cursor):
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER,
            plan_id INTEGER,
            integration TEXT NOT NULL DEFAULT '',
            node TEXT NOT NULL DEFAULT '',
            type TEXT NOT NULL DEFAULT '',
            vmid TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            guest_name TEXT NOT NULL DEFAULT '',
            guest_ips TEXT NOT NULL DEFAULT '',
            target_source TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL DEFAULT 0,
            state TEXT NOT NULL DEFAULT 'creating',
            created_by TEXT NOT NULL DEFAULT '',
            rolled_back_at INTEGER NOT NULL DEFAULT 0,
            deleted_at INTEGER NOT NULL DEFAULT 0,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT ''
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_snapshots_request '
                   'ON snapshots(request_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_snapshots_state '
                   'ON snapshots(state, expires_at)')


def create_snapshot(request_id=None, plan_id=None, integration='', node='',
                    type_='', vmid='', name='', description='', guest_name='',
                    guest_ips='', target_source='', expires_at=0,
                    created_by='', now=None) -> int:
    """Insert the row *before* Proxmox is called, so a crash leaves a record."""
    now = int(now or time.time())
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO snapshots (request_id, plan_id, integration, node,
                type, vmid, name, description, guest_name, guest_ips,
                target_source, created_at, expires_at, state, created_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'creating',?)
        ''', (request_id, plan_id, integration, node, type_, vmid, name,
              description, guest_name, guest_ips, target_source, now,
              int(expires_at or 0), created_by or ''))
        conn.commit()
        return cur.lastrowid


def get_snapshot(snapshot_id):
    with db_conn() as conn:
        row = conn.execute('SELECT * FROM snapshots WHERE id = ?',
                           (int(snapshot_id),)).fetchone()
        return dict(row) if row else None


def snapshots_for_request(request_id):
    with db_conn() as conn:
        rows = conn.execute(
            'SELECT * FROM snapshots WHERE request_id = ? ORDER BY id DESC',
            (int(request_id),)).fetchall()
        return [dict(r) for r in rows]


def set_snapshot_state(snapshot_id, state, error=None, **fields):
    """Move one row to `state`, optionally with extra columns.

    The caller decides the transition; this only writes it.
    """
    if state not in STATES:
        raise ValueError('bad_state')
    sets, params = ['state = ?'], [state]
    if error is not None:
        sets.append('last_error = ?')
        params.append(str(error)[:300])
    for key, value in (fields or {}).items():
        if key not in ('expires_at', 'rolled_back_at', 'deleted_at', 'attempts'):
            raise ValueError('bad_field:%s' % key)
        sets.append('%s = ?' % key)
        params.append(int(value or 0))
    params.append(int(snapshot_id))
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute('UPDATE snapshots SET %s WHERE id = ?' % ', '.join(sets),
                    tuple(params))
        conn.commit()
        return cur.rowcount == 1


def due_snapshots(now=None, limit=20):
    """Rows the expiry job owns: active or failed, and past their expiry."""
    now = int(now or time.time())
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM snapshots WHERE state IN ('active', 'failed') "
            "AND expires_at > 0 AND expires_at <= ? ORDER BY expires_at "
            "LIMIT ?", (now, int(limit))).fetchall()
        return [dict(r) for r in rows]


def stale_creating(older_than, now=None):
    """Rows whose create was never seen to finish (a crash between the insert
    and the first poll). They are marked failed by the startup sweep, never
    deleted."""
    now = int(now or time.time())
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM snapshots WHERE state = 'creating' AND created_at <= ?",
            (now - int(older_than),)).fetchall()
        return [dict(r) for r in rows]


def bump_attempts(snapshot_id):
    with db_conn() as conn:
        conn.execute('UPDATE snapshots SET attempts = attempts + 1 WHERE id = ?',
                     (int(snapshot_id),))
        conn.commit()

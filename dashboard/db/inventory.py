"""F14: the inventory cache.

Two tables, both replaceable: `inventory_items` holds one row per thing a
source knows about (a gateway, a Proxmox guest, an NPM proxy host, a Pi-hole
record, an Omada client); `inventory_sources` holds the last refresh result per
source so a failed source keeps its last good rows and is reported as stale
instead of vanishing.

Nothing here is a decision: the inventory is a read-only map, refreshed from
read-only source calls.
"""
import json
import time

from db.core import db_conn


def init_inventory_tables(cursor):
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS inventory_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            kind TEXT NOT NULL,
            key TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            ips TEXT NOT NULL DEFAULT '',
            fqdns TEXT NOT NULL DEFAULT '',
            host TEXT NOT NULL DEFAULT '',
            vmid TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL DEFAULT '',
            extra_json TEXT NOT NULL DEFAULT '{}',
            seen_at INTEGER NOT NULL,
            UNIQUE(source, kind, key)
        )
    ''')
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_inventory_items_source '
        'ON inventory_items(source)')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS inventory_sources (
            source TEXT PRIMARY KEY,
            label TEXT NOT NULL DEFAULT '',
            last_ok INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            item_count INTEGER NOT NULL DEFAULT 0
        )
    ''')


def _join(value):
    return ','.join(str(v) for v in (value or []) if str(v).strip())


def _split(value):
    return [part for part in str(value or '').split(',') if part]


def replace_source_items(source: str, items, now: int = None) -> int:
    """Swap one source's rows in one transaction (delete + insert)."""
    now = int(now or time.time())
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM inventory_items WHERE source = ?', (source,))
        for item in items or []:
            cursor.execute('''
                INSERT OR REPLACE INTO inventory_items
                    (source, kind, key, name, ips, fqdns, host, vmid, url,
                     extra_json, seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                source,
                (item.get('kind') or '')[:32],
                str(item.get('key') or '')[:160],
                (item.get('name') or '')[:200],
                _join(item.get('ips'))[:400],
                _join(item.get('fqdns'))[:400],
                (item.get('host') or '')[:120],
                str(item.get('vmid') or '')[:32],
                (item.get('url') or '')[:400],
                json.dumps(item.get('extra') or {}, separators=(',', ':'))[:2000],
                now,
            ))
        conn.commit()
        return len(items or [])


def record_source(source: str, label: str, ok: bool, reason: str = '',
                  count: int = 0, now: int = None):
    now = int(now or time.time())
    with db_conn() as conn:
        cursor = conn.cursor()
        if ok:
            cursor.execute('''
                INSERT INTO inventory_sources (source, label, last_ok, last_error, item_count)
                VALUES (?, ?, ?, '', ?)
                ON CONFLICT(source) DO UPDATE SET
                    label = excluded.label, last_ok = excluded.last_ok,
                    last_error = '', item_count = excluded.item_count
            ''', (source, label, now, count))
        else:
            cursor.execute('''
                INSERT INTO inventory_sources (source, label, last_ok, last_error, item_count)
                VALUES (?, ?, 0, ?, 0)
                ON CONFLICT(source) DO UPDATE SET
                    label = excluded.label, last_error = excluded.last_error
            ''', (source, label, (reason or '')[:300]))
        conn.commit()


def get_inventory_items():
    with db_conn() as conn:
        cursor = conn.cursor()
        rows = cursor.execute('SELECT * FROM inventory_items').fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item['ips'] = _split(item.get('ips'))
        item['fqdns'] = _split(item.get('fqdns'))
        try:
            item['extra'] = json.loads(item.get('extra_json') or '{}')
        except ValueError:
            item['extra'] = {}
        out.append(item)
    return out


def get_inventory_sources():
    with db_conn() as conn:
        cursor = conn.cursor()
        rows = cursor.execute(
            'SELECT * FROM inventory_sources ORDER BY source ASC').fetchall()
    return [dict(row) for row in rows]


def clear_inventory():
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM inventory_items')
        cursor.execute('DELETE FROM inventory_sources')
        conn.commit()

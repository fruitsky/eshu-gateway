"""Persistence for Eshu-owned commute profiles and notification state."""
from __future__ import annotations

import json
import time

from db.core import db_conn


def init_commute_tables(cursor):
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS commute_profiles (
            person TEXT PRIMARY KEY,
            config_json TEXT NOT NULL,
            updated_at INTEGER NOT NULL,
            updated_by TEXT NOT NULL DEFAULT ''
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS commute_notify_state (
            person TEXT NOT NULL,
            day TEXT NOT NULL,
            slot TEXT NOT NULL,
            sent_at INTEGER NOT NULL,
            PRIMARY KEY (person, day, slot)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS commute_profile_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person TEXT NOT NULL,
            agent TEXT NOT NULL DEFAULT '',
            changed_at INTEGER NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            before_json TEXT NOT NULL DEFAULT '',
            after_json TEXT NOT NULL DEFAULT ''
        )
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS ix_commute_audit_person
        ON commute_profile_audit(person, id)
    ''')


def get_profile(person: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT config_json FROM commute_profiles WHERE person = ?', (person,))
        row = cursor.fetchone()
    if not row:
        return None
    try:
        data = json.loads(row['config_json'])
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def get_profile_row(person: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT * FROM commute_profiles WHERE person = ?', (person,))
        row = cursor.fetchone()
    return dict(row) if row else None


def list_profiles():
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT person, config_json FROM commute_profiles ORDER BY person')
        rows = cursor.fetchall()
    out = {}
    for row in rows:
        try:
            data = json.loads(row['config_json'])
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            out[row['person']] = data
    return out


def save_profile(person: str, config: dict, agent: str = '') -> dict:
    """Upsert a profile and write an audit row with the before/after diff."""
    before = get_profile(person)
    before_json = json.dumps(before, sort_keys=True, ensure_ascii=False) if before else ''
    after_json = json.dumps(config, sort_keys=True, ensure_ascii=False)
    changed = []
    if before:
        for key in sorted(set(before) | set(config)):
            if before.get(key) != config.get(key):
                changed.append(key)
    else:
        changed = sorted(config)
    summary = ('created' if not before else
               ('no changes' if not changed else 'changed: ' + ', '.join(changed)))
    now = int(time.time())
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('BEGIN IMMEDIATE')
        cursor.execute('''
            INSERT INTO commute_profiles (person, config_json, updated_at, updated_by)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(person) DO UPDATE SET
                config_json = excluded.config_json,
                updated_at = excluded.updated_at,
                updated_by = excluded.updated_by
        ''', (person, after_json, now, agent or ''))
        cursor.execute('''
            INSERT INTO commute_profile_audit
                (person, agent, changed_at, summary, before_json, after_json)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (person, agent or '', now, summary, before_json, after_json))
        conn.commit()
    return get_profile(person) or config


def was_sent(person: str, day: str, slot: str) -> bool:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT 1 FROM commute_notify_state
            WHERE person = ? AND day = ? AND slot = ?
        ''', (person, day, slot))
        return cursor.fetchone() is not None


def mark_sent(person: str, day: str, slot: str) -> None:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO commute_notify_state (person, day, slot, sent_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(person, day, slot) DO UPDATE SET sent_at = excluded.sent_at
        ''', (person, day, slot, int(time.time())))
        conn.commit()


def clear_sent(person: str = None, day: str = None) -> None:
    """Testing/repair helper: forget sent slots for a person/day."""
    with db_conn() as conn:
        cursor = conn.cursor()
        if person and day:
            cursor.execute(
                'DELETE FROM commute_notify_state WHERE person = ? AND day = ?',
                (person, day))
        elif person:
            cursor.execute(
                'DELETE FROM commute_notify_state WHERE person = ?', (person,))
        else:
            cursor.execute('DELETE FROM commute_notify_state')
        conn.commit()


def list_audit(person: str = None, limit: int = 20):
    with db_conn() as conn:
        cursor = conn.cursor()
        if person:
            cursor.execute('''
                SELECT id, person, agent, changed_at, summary
                FROM commute_profile_audit WHERE person = ?
                ORDER BY id DESC LIMIT ?
            ''', (person, max(1, int(limit))))
        else:
            cursor.execute('''
                SELECT id, person, agent, changed_at, summary
                FROM commute_profile_audit
                ORDER BY id DESC LIMIT ?
            ''', (max(1, int(limit)),))
        return [dict(row) for row in cursor.fetchall()]

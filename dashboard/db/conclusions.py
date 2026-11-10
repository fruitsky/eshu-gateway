"""F13: session conclusions and runbooks.

A conclusion is what the agent says the session *meant* (outcome, what worked,
what did not, root cause, fix); the operator confirms, edits or rejects it.
Only confirmed conclusions — and runbooks promoted from them — are ever served
to agents.

Storage is two tables plus an FTS5 index over the conclusion text and the
session goal, so `similar_incidents` ranks by relevance without a new
dependency. The FTS row is kept in step with the table by hand (there is no
`content=` external table) so a rowid always maps to a conclusion id.
"""
import json
import re
import time

from db.core import db_conn

CONCLUSION_STATUSES = ('draft', 'confirmed', 'rejected')
# S2: a search string is matched term by term; cap the terms so one query can
# never grow the SQL without bound.
MAX_TERMS = 8


class ConclusionError(Exception):
    """A refused conclusion operation; `.code` is the stable reason."""

    def __init__(self, code, message=''):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def init_conclusions_tables(cursor):
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS session_conclusions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            agent TEXT NOT NULL DEFAULT '',
            goal TEXT NOT NULL DEFAULT '',
            outcome TEXT NOT NULL DEFAULT 'partial',
            summary TEXT NOT NULL DEFAULT '',
            worked_json TEXT NOT NULL DEFAULT '[]',
            did_not_work_json TEXT NOT NULL DEFAULT '[]',
            root_cause TEXT NOT NULL DEFAULT '',
            fix TEXT NOT NULL DEFAULT '',
            follow_ups_json TEXT NOT NULL DEFAULT '[]',
            tags_json TEXT NOT NULL DEFAULT '[]',
            runbook_candidate INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'draft',
            operator_note TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            confirmed_at INTEGER,
            confirmed_by TEXT NOT NULL DEFAULT '',
            UNIQUE(session_id, agent)
        )
    ''')
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_conclusions_session '
        'ON session_conclusions(session_id)')
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_conclusions_status '
        'ON session_conclusions(status)')
    cursor.execute('''
        CREATE VIRTUAL TABLE IF NOT EXISTS session_conclusions_fts
        USING fts5(summary, root_cause, fix, tags, goal)
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS runbooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conclusion_id INTEGER NOT NULL,
            session_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            when_to_use TEXT NOT NULL DEFAULT '',
            tags_json TEXT NOT NULL DEFAULT '[]',
            steps_json TEXT NOT NULL DEFAULT '[]',
            dead_ends_json TEXT NOT NULL DEFAULT '[]',
            created_at INTEGER NOT NULL,
            created_by TEXT NOT NULL DEFAULT '',
            UNIQUE(conclusion_id)
        )
    ''')


def _loads(value, default):
    try:
        data = json.loads(value or '[]')
    except (TypeError, ValueError):
        return default
    return data if isinstance(data, list) else default


def _conclusion(row):
    if not row:
        return None
    item = dict(row)
    item['worked'] = _loads(item.pop('worked_json', '[]'), [])
    item['did_not_work'] = _loads(item.pop('did_not_work_json', '[]'), [])
    item['follow_ups'] = _loads(item.pop('follow_ups_json', '[]'), [])
    item['tags'] = _loads(item.pop('tags_json', '[]'), [])
    item['runbook_candidate'] = bool(item.get('runbook_candidate'))
    return item


def _runbook(row):
    if not row:
        return None
    item = dict(row)
    item['tags'] = _loads(item.pop('tags_json', '[]'), [])
    item['steps'] = _loads(item.pop('steps_json', '[]'), [])
    item['dead_ends'] = _loads(item.pop('dead_ends_json', '[]'), [])
    return item


def _fts_sync(cursor, conclusion_id, summary, root_cause, fix, tags, goal):
    cursor.execute('DELETE FROM session_conclusions_fts WHERE rowid = ?',
                   (conclusion_id,))
    cursor.execute('''
        INSERT INTO session_conclusions_fts
            (rowid, summary, root_cause, fix, tags, goal)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (conclusion_id, summary or '', root_cause or '', fix or '',
          ' '.join(tags or []), goal or ''))


def get_conclusion(conclusion_id):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM session_conclusions WHERE id = ?',
                       (int(conclusion_id),))
        return _conclusion(cursor.fetchone())


def get_for_agent(session_id, agent):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT * FROM session_conclusions WHERE session_id = ? AND agent = ?',
            ((session_id or '')[:64], (agent or '')[:64]))
        return _conclusion(cursor.fetchone())


def list_for_session(session_id):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT * FROM session_conclusions WHERE session_id = ? '
            'ORDER BY id ASC', ((session_id or '')[:64],))
        rows = [_conclusion(row) for row in cursor.fetchall()]
        for item in rows:
            cursor.execute('SELECT id FROM runbooks WHERE conclusion_id = ?',
                           (item['id'],))
            runbook = cursor.fetchone()
            item['runbook_id'] = runbook['id'] if runbook else None
        return rows


def upsert_draft(session_id: str, agent: str, goal: str, payload: dict,
                 now: int = None) -> dict:
    """Create or replace this agent's draft. A confirmed conclusion is final."""
    now = int(now or time.time())
    sid = (session_id or '')[:64]
    owner = (agent or '')[:64]
    existing = get_for_agent(sid, owner)
    if existing and existing['status'] == 'confirmed':
        raise ConclusionError(
            'already_confirmed',
            'This session already has a confirmed conclusion from this agent.')
    with db_conn() as conn:
        cursor = conn.cursor()
        values = (
            (payload.get('outcome') or 'partial')[:32],
            (payload.get('summary') or '')[:8000],
            json.dumps(payload.get('worked') or [], separators=(',', ':'))[:20000],
            json.dumps(payload.get('did_not_work') or [],
                       separators=(',', ':'))[:20000],
            (payload.get('root_cause') or '')[:8000],
            (payload.get('fix') or '')[:8000],
            json.dumps(payload.get('follow_ups') or [],
                       separators=(',', ':'))[:20000],
            json.dumps(payload.get('tags') or [], separators=(',', ':'))[:4000],
            1 if payload.get('runbook_candidate') else 0,
            now,
        )
        if existing:
            cursor.execute('''
                UPDATE session_conclusions SET
                    outcome = ?, summary = ?, worked_json = ?,
                    did_not_work_json = ?, root_cause = ?, fix = ?,
                    follow_ups_json = ?, tags_json = ?,
                    runbook_candidate = ?, updated_at = ?,
                    status = 'draft', operator_note = '',
                    confirmed_at = NULL, confirmed_by = ''
                WHERE id = ?
            ''', values + (existing['id'],))
            conclusion_id = existing['id']
        else:
            cursor.execute('''
                INSERT INTO session_conclusions
                    (session_id, agent, goal, outcome, summary, worked_json,
                     did_not_work_json, root_cause, fix, follow_ups_json,
                     tags_json, runbook_candidate, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)
            ''', (sid, owner, (goal or '')[:160]) + values[:9] + (now, now))
            conclusion_id = cursor.lastrowid
        _fts_sync(cursor, conclusion_id, payload.get('summary'),
                  payload.get('root_cause'), payload.get('fix'),
                  payload.get('tags'), goal)
        conn.commit()
    return get_conclusion(conclusion_id)


def confirm_conclusion(conclusion_id, note: str = '', by: str = '',
                       now: int = None) -> dict:
    now = int(now or time.time())
    if not get_conclusion(conclusion_id):
        raise ConclusionError('not_found', 'No conclusion with that id.')
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE session_conclusions SET status = 'confirmed', "
            "operator_note = ?, confirmed_at = ?, confirmed_by = ?, "
            "updated_at = ? WHERE id = ? AND status = 'draft'",
            ((note or '')[:2000], now, (by or '')[:64], now,
             int(conclusion_id)))
        conn.commit()
        if cursor.rowcount != 1:
            raise ConclusionError('bad_state',
                                  'Only a draft conclusion can be confirmed.')
    return get_conclusion(conclusion_id)


def reject_conclusion(conclusion_id, note: str = '', by: str = '',
                      now: int = None) -> dict:
    now = int(now or time.time())
    if not get_conclusion(conclusion_id):
        raise ConclusionError('not_found', 'No conclusion with that id.')
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE session_conclusions SET status = 'rejected', "
            "operator_note = ?, confirmed_by = ?, updated_at = ? "
            "WHERE id = ? AND status = 'draft'",
            ((note or '')[:2000], (by or '')[:64], now, int(conclusion_id)))
        conn.commit()
        if cursor.rowcount != 1:
            raise ConclusionError('bad_state',
                                  'Only a draft conclusion can be rejected.')
    return get_conclusion(conclusion_id)


def edit_conclusion(conclusion_id, summary=None, root_cause=None, fix=None,
                    note: str = '', now: int = None) -> dict:
    """Operator edit of a draft: the agent's text changes, the status stays."""
    now = int(now or time.time())
    item = get_conclusion(conclusion_id)
    if not item:
        raise ConclusionError('not_found', 'No conclusion with that id.')
    if item['status'] != 'draft':
        raise ConclusionError('bad_state', 'Only a draft can be edited.')
    new_summary = item['summary'] if summary is None else (summary or '')[:8000]
    new_root = item['root_cause'] if root_cause is None else (root_cause or '')[:8000]
    new_fix = item['fix'] if fix is None else (fix or '')[:8000]
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE session_conclusions SET summary = ?, root_cause = ?, fix = ?,
                operator_note = ?, updated_at = ?
            WHERE id = ?
        ''', (new_summary, new_root, new_fix, (note or '')[:2000], now,
              int(conclusion_id)))
        _fts_sync(cursor, int(conclusion_id), new_summary, new_root, new_fix,
                  item['tags'], item['goal'])
        conn.commit()
    return get_conclusion(conclusion_id)


def create_runbook(conclusion_id, title: str, when_to_use: str, steps,
                   dead_ends, tags=None, by: str = '', now: int = None) -> dict:
    now = int(now or time.time())
    item = get_conclusion(conclusion_id)
    if not item:
        raise ConclusionError('not_found', 'No conclusion with that id.')
    if item['status'] != 'confirmed':
        raise ConclusionError('not_confirmed',
                              'Only a confirmed conclusion can become a runbook.')
    if not item['runbook_candidate']:
        raise ConclusionError('not_a_candidate',
                              'The conclusion was not marked as a runbook candidate.')
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO runbooks
                (conclusion_id, session_id, title, when_to_use, tags_json,
                 steps_json, dead_ends_json, created_at, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(conclusion_id) DO UPDATE SET
                title = excluded.title, when_to_use = excluded.when_to_use,
                tags_json = excluded.tags_json, steps_json = excluded.steps_json,
                dead_ends_json = excluded.dead_ends_json,
                created_at = excluded.created_at, created_by = excluded.created_by
        ''', (int(conclusion_id), item['session_id'], (title or '')[:200],
              (when_to_use or '')[:2000],
              json.dumps(tags or item['tags'], separators=(',', ':'))[:4000],
              json.dumps(steps or [], separators=(',', ':'))[:60000],
              json.dumps(dead_ends or [], separators=(',', ':'))[:20000],
              now, (by or '')[:64]))
        conn.commit()
        cursor.execute('SELECT id FROM runbooks WHERE conclusion_id = ?',
                       (int(conclusion_id),))
        return cursor.fetchone()['id']


def get_runbook(runbook_id):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT r.*, c.status AS conclusion_status, c.outcome, c.summary
            FROM runbooks r JOIN session_conclusions c ON c.id = r.conclusion_id
            WHERE r.id = ?
        ''', (int(runbook_id),))
        row = cursor.fetchone()
        if not row or row['conclusion_status'] != 'confirmed':
            return None
        return _runbook(row)


def list_runbooks_for_session(session_id):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT r.* FROM runbooks r
            JOIN session_conclusions c ON c.id = r.conclusion_id
            WHERE r.session_id = ? AND c.status = 'confirmed'
            ORDER BY r.id ASC
        ''', ((session_id or '')[:64],))
        return [_runbook(row) for row in cursor.fetchall()]


def _fts_match(query) -> str:
    terms = [t.replace('"', '') for t in (query or '').split() if t]
    return ' '.join('"%s"' % term for term in terms)


def search_confirmed(query, limit: int = 5):
    """Confirmed conclusions by FTS rank. Empty query returns nothing."""
    match = _fts_match(query)
    if not match:
        return []
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT c.*, bm25(session_conclusions_fts) AS rank
            FROM session_conclusions_fts
            JOIN session_conclusions c ON c.id = session_conclusions_fts.rowid
            WHERE session_conclusions_fts MATCH ? AND c.status = 'confirmed'
            ORDER BY rank ASC, c.confirmed_at DESC
            LIMIT ?
        ''', (match, int(limit)))
        return [_conclusion(row) for row in cursor.fetchall()]


def search_runbooks(query, tags=None, limit: int = 5):
    where = ["c.status = 'confirmed'"]
    params = []
    if query:
        # S2: match per term, not the whole string. "nginx reload" must find a
        # runbook whose title says "reload nginx" and whose summary says
        # "certificate" -- the old single LIKE required the exact phrase to
        # appear inside one field.
        terms = [term for term in re.split(r'\s+', query.strip()) if term]
        for term in terms[:MAX_TERMS]:
            needle = '%' + term + '%'
            where.append('(r.title LIKE ? OR r.when_to_use LIKE ? '
                         'OR r.tags_json LIKE ? OR c.summary LIKE ?)')
            params += [needle] * 4
    for tag in (tags or []):
        where.append('r.tags_json LIKE ?')
        params.append('%%"%s"%%' % str(tag).replace('"', ''))
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT r.* FROM runbooks r
            JOIN session_conclusions c ON c.id = r.conclusion_id
            WHERE %s
            ORDER BY r.created_at DESC, r.id DESC
            LIMIT ?
        ''' % ' AND '.join(where), tuple(params) + (int(limit),))
        return [_runbook(row) for row in cursor.fetchall()]

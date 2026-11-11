"""F16: recipes, their immutable versions, and runs.

A recipe is a row plus immutable versions; only an approved version can run.
A run keeps its own parameters (normalised), the version hash and one row per
step, so a later edit never changes what a run did.
"""
import json
import time

from db.core import db_conn

MAX_STEPS = 10
MAX_SSH_STEPS = 3
MAX_VERSIONS = 200
RUN_STATUSES = ('pending', 'running', 'ok', 'failed', 'stopped', 'rolled_back')


class RecipeError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def init_recipes_tables(cursor):
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'draft',
            current_version_id INTEGER NOT NULL DEFAULT 0,
            owner TEXT NOT NULL DEFAULT '',
            auto_run INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS recipe_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL,
            version INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            document_json TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            approved_at INTEGER NOT NULL DEFAULT 0,
            approved_by TEXT NOT NULL DEFAULT '',
            UNIQUE (recipe_id, version)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS recipe_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL,
            version_id INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'run',
            of_run_id INTEGER NOT NULL DEFAULT 0,
            session_id TEXT NOT NULL DEFAULT '',
            agent TEXT NOT NULL DEFAULT '',
            origin TEXT NOT NULL DEFAULT 'agent',
            plan_id INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            parameters_json TEXT NOT NULL DEFAULT '{}',
            failed_step TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL,
            started_at INTEGER NOT NULL DEFAULT 0,
            finished_at INTEGER NOT NULL DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS recipe_run_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            step_id TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            created INTEGER NOT NULL DEFAULT 0,
            result_json TEXT NOT NULL DEFAULT '',
            check_state TEXT NOT NULL DEFAULT '',
            request_id INTEGER NOT NULL DEFAULT 0,
            integration_call_id INTEGER NOT NULL DEFAULT 0,
            error TEXT NOT NULL DEFAULT ''
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_recipe_versions_recipe '
                   'ON recipe_versions(recipe_id, version)')
    # F16 AW1: an operator-started run has no agent identity to bound an SSH
    # step, so the origin is recorded; pre-existing rows are agent-started.
    try:
        cursor.execute("ALTER TABLE recipe_runs ADD COLUMN origin TEXT "
                       "NOT NULL DEFAULT 'agent'")
    except Exception:
        pass
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_recipe_runs_recipe '
                   'ON recipe_runs(recipe_id, created_at)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_recipe_runs_status '
                   'ON recipe_runs(status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_recipe_run_steps_run '
                   'ON recipe_run_steps(run_id, step_id)')


# ── recipes and versions ───────────────────────────────────────────────

def get_recipe(recipe_id=None, name=None):
    with db_conn() as conn:
        if recipe_id is not None:
            row = conn.execute('SELECT * FROM recipes WHERE id = ?',
                               (int(recipe_id),)).fetchone()
        else:
            row = conn.execute('SELECT * FROM recipes WHERE name = ?',
                               (str(name or ''),)).fetchone()
        return dict(row) if row else None


def list_recipes(include_disabled=True):
    sql = 'SELECT * FROM recipes'
    if not include_disabled:
        sql += " WHERE status != 'disabled'"
    sql += ' ORDER BY name ASC'
    with db_conn() as conn:
        return [dict(row) for row in conn.execute(sql).fetchall()]


def create_recipe(name, owner, document):
    """Insert a new draft recipe with version 1."""
    now = int(time.time())
    with db_conn() as conn:
        cur = conn.cursor()
        if cur.execute('SELECT 1 FROM recipes WHERE name = ?', (name,)).fetchone():
            raise RecipeError('recipe_exists', 'a recipe with that name already exists')
        cur.execute('''
            INSERT INTO recipes (name, status, current_version_id, owner,
                                 auto_run, created_at, updated_at)
            VALUES (?, 'draft', 0, ?, 0, ?, ?)
        ''', (name, (owner or '')[:64], now, now))
        recipe_id = cur.lastrowid
        version_id = _insert_version(cur, recipe_id, 1, document, now)
        cur.execute('UPDATE recipes SET current_version_id = ? WHERE id = ?',
                    (version_id, recipe_id))
        conn.commit()
    return get_recipe(recipe_id)


def _canonical(document):
    return json.dumps(document, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False)


def content_hash(document):
    import hashlib
    return hashlib.sha256(_canonical(document).encode('utf-8')).hexdigest()


def _insert_version(cur, recipe_id, version, document, now, approved_at=0,
                    approved_by=''):
    cur.execute('''
        INSERT INTO recipe_versions (recipe_id, version, content_hash,
                                     document_json, created_at, approved_at,
                                     approved_by)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (recipe_id, version, content_hash(document), _canonical(document),
          now, approved_at, approved_by))
    return cur.lastrowid


def add_version(recipe_id, document, owner=''):
    """A draft version; an edit always creates a new, unapproved version."""
    now = int(time.time())
    with db_conn() as conn:
        cur = conn.cursor()
        recipe = cur.execute('SELECT * FROM recipes WHERE id = ?',
                             (int(recipe_id),)).fetchone()
        if not recipe:
            raise RecipeError('not_found', 'recipe not found')
        last = cur.execute('SELECT MAX(version) AS v FROM recipe_versions '
                           'WHERE recipe_id = ?', (int(recipe_id),)).fetchone()
        version = int((last['v'] if last else 0) or 0) + 1
        if version > MAX_VERSIONS:
            raise RecipeError('too_many_versions', 'too many versions')
        digest = content_hash(document)
        same = cur.execute('SELECT * FROM recipe_versions WHERE recipe_id = ? '
                           'AND content_hash = ?', (int(recipe_id), digest)).fetchone()
        if same:
            raise RecipeError('unchanged_version', 'the document has not changed')
        version_id = _insert_version(cur, int(recipe_id), version, document, now)
        cur.execute('''UPDATE recipes SET status = 'draft', updated_at = ?
                       WHERE id = ?''', (now, int(recipe_id)))
        cur.execute('UPDATE recipes SET current_version_id = ? WHERE id = ?',
                    (version_id, int(recipe_id)))
        conn.commit()
    return get_version(version_id)


def get_version(version_id):
    with db_conn() as conn:
        row = conn.execute('SELECT * FROM recipe_versions WHERE id = ?',
                           (int(version_id),)).fetchone()
        if not row:
            return None
        out = dict(row)
        try:
            out['document'] = json.loads(out.get('document_json') or '{}')
        except ValueError:
            out['document'] = {}
        return out


def current_version(recipe_id):
    recipe = get_recipe(recipe_id)
    if not recipe or not recipe.get('current_version_id'):
        return None
    return get_version(recipe['current_version_id'])


def approve_version(recipe_id, version, actor='operator'):
    """Approve one version; it becomes the recipe's runnable version."""
    now = int(time.time())
    with db_conn() as conn:
        cur = conn.cursor()
        row = cur.execute('SELECT * FROM recipe_versions WHERE recipe_id = ? '
                          'AND version = ?', (int(recipe_id), int(version))).fetchone()
        if not row:
            raise RecipeError('not_found', 'version not found')
        cur.execute('''UPDATE recipe_versions SET approved_at = ?, approved_by = ?
                       WHERE id = ?''', (now, (actor or 'operator')[:64], row['id']))
        cur.execute('''UPDATE recipes SET status = 'approved',
                       current_version_id = ?, updated_at = ? WHERE id = ?''',
                    (row['id'], now, int(recipe_id)))
        conn.commit()
    return get_version(row['id'])


def disable_recipe(recipe_id):
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE recipes SET status = 'disabled', updated_at = ? "
                    "WHERE id = ?", (int(time.time()), int(recipe_id)))
        conn.commit()
        return cur.rowcount == 1


def set_auto_run(recipe_id, enabled):
    """auto_run is locked off: this always refuses (design §3.3)."""
    raise RecipeError('auto_run_locked',
                      'auto_run is not enabled in v1; it comes back to security '
                      'before it is ever switched on')


# ── runs ───────────────────────────────────────────────────────────────

def create_run(recipe, version, session_id, agent, parameters, kind='run',
               of_run_id=0, origin='agent'):
    now = int(time.time())
    origin = 'operator' if origin == 'operator' else 'agent'
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO recipe_runs (recipe_id, version_id, content_hash, kind,
                                     of_run_id, session_id, agent, origin, status,
                                     parameters_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
        ''', (int(recipe['id']), int(version['id']), version['content_hash'],
              kind, int(of_run_id or 0), (session_id or '')[:64],
              (agent or '')[:64], origin,
              json.dumps(parameters, sort_keys=True, separators=(',', ':')),
              now))
        run_id = cur.lastrowid
        conn.commit()
    return run_id


def add_run_step(run_id, step_id, kind, title=''):
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute('''INSERT INTO recipe_run_steps (run_id, step_id, kind, title)
                       VALUES (?, ?, ?, ?)''',
                    (int(run_id), str(step_id), kind, (title or '')[:200]))
        conn.commit()
        return cur.lastrowid


def get_run(run_id):
    with db_conn() as conn:
        row = conn.execute('SELECT * FROM recipe_runs WHERE id = ?',
                           (int(run_id),)).fetchone()
        if not row:
            return None
        out = dict(row)
        try:
            out['parameters'] = json.loads(out.get('parameters_json') or '{}')
        except ValueError:
            out['parameters'] = {}
        out['steps'] = [dict(step) for step in conn.execute(
            'SELECT * FROM recipe_run_steps WHERE run_id = ? ORDER BY id',
            (int(run_id),)).fetchall()]
        return out


def list_runs(recipe_id=None, limit=50):
    sql = 'SELECT * FROM recipe_runs'
    params = []
    if recipe_id is not None:
        sql += ' WHERE recipe_id = ?'
        params.append(int(recipe_id))
    sql += ' ORDER BY id DESC LIMIT ?'
    params.append(int(limit))
    with db_conn() as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def set_run(run_id, **fields):
    allowed = {'status', 'plan_id', 'failed_step', 'started_at', 'finished_at',
               'agent'}
    sets, params = [], []
    for key, value in fields.items():
        if key not in allowed:
            raise KeyError(key)
        sets.append('%s = ?' % key)
        params.append(value)
    if not sets:
        return
    params.append(int(run_id))
    with db_conn() as conn:
        conn.execute('UPDATE recipe_runs SET %s WHERE id = ?' % ', '.join(sets),
                     params)
        conn.commit()


def set_step(run_id, step_id, **fields):
    allowed = {'status', 'created', 'result_json', 'check_state',
               'request_id', 'integration_call_id', 'error', 'title'}
    sets, params = [], []
    for key, value in fields.items():
        if key not in allowed:
            raise KeyError(key)
        sets.append('%s = ?' % key)
        params.append(value)
    if not sets:
        return
    params.extend([int(run_id), str(step_id)])
    with db_conn() as conn:
        conn.execute('UPDATE recipe_run_steps SET %s WHERE run_id = ? AND step_id = ?'
                     % ', '.join(sets), params)
        conn.commit()


def pending_runs_for_approved_plans(limit=5):
    """Runs whose plan is approved but which have not started."""
    with db_conn() as conn:
        rows = conn.execute('''
            SELECT r.* FROM recipe_runs r
            JOIN session_plans p ON p.id = r.plan_id
            WHERE r.status = 'pending' AND p.status = 'approved' AND p.until > ?
            ORDER BY r.id LIMIT ?
        ''', (int(time.time()), int(limit))).fetchall()
        return [dict(row) for row in rows]


def expired_waiting_runs(now=None):
    """Runs whose plan never got approved: denied/ended/expired."""
    now = int(now or time.time())
    with db_conn() as conn:
        rows = conn.execute('''
            SELECT r.* FROM recipe_runs r
            LEFT JOIN session_plans p ON p.id = r.plan_id
            WHERE r.status = 'pending' AND p.id IS NOT NULL
              AND (p.status IN ('denied', 'ended', 'stopped') OR p.until <= ?)
        ''', (now,)).fetchall()
        return [dict(row) for row in rows]


def list_versions(recipe_id):
    with db_conn() as conn:
        rows = conn.execute('SELECT * FROM recipe_versions WHERE recipe_id = ? '
                            'ORDER BY version', (int(recipe_id),)).fetchall()
        return [dict(row) for row in rows]


def get_version_by_number(recipe_id, version):
    with db_conn() as conn:
        row = conn.execute('SELECT * FROM recipe_versions WHERE recipe_id = ? '
                           'AND version = ?', (int(recipe_id), int(version))).fetchone()
        if not row:
            return None
        out = dict(row)
        try:
            out['document'] = json.loads(out.get('document_json') or '{}')
        except ValueError:
            out['document'] = {}
        return out

"""Inner-target attribution (F7).

A command wrapped in `pct exec` / `qm guest exec` / `docker exec` runs inside a
container or VM, but the request row records the host that ran the wrapper.
This module derives the real target from the command (F1's breakdown) and
backfills rows that predate the `requests.inner_target` column.

The backfill runs once, in the background at startup: it is idempotent (only
NULL rows are touched), batched by distinct command, and guarded by a `meta`
key so a restart does not rescan a finished database. It never blocks startup
or the request path.
"""
import threading
import time

from db.core import db_conn

BACKFILL_META_KEY = 'inner_target_backfill_done'
BATCH_SIZE = 500


def inner_target_for(command: str) -> str:
    """The recorded target for one command: `pct:103`, `docker:web01`,
    `qm:201`, `multi` when a command reaches several, or '' for none."""
    from core.cmd_parse import breakdown
    targets = breakdown(command or '').get('inner_targets') or []
    if not targets:
        return ''
    if len(targets) == 1:
        return targets[0]
    return 'multi'


def backfill_inner_targets(batch_size: int = BATCH_SIZE) -> dict:
    """Fill `inner_target` for rows that predate the column (NULL).

    Returns `{'filled': <distinct commands>, 'skipped': bool}`. Safe to call
    twice: the second call sees the completion key (or no NULL rows) and does
    nothing.
    """
    with db_conn() as conn:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = ?", (BACKFILL_META_KEY,)).fetchone()
        if row:
            return {'filled': 0, 'skipped': True}

    filled = 0
    while True:
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT command FROM requests "
                "WHERE inner_target IS NULL LIMIT ?", (batch_size,)).fetchall()
        if not rows:
            break
        for row in rows:
            command = row[0]
            value = inner_target_for(command)
            with db_conn() as conn:
                conn.execute(
                    "UPDATE requests SET inner_target = ? "
                    "WHERE command = ? AND inner_target IS NULL",
                    (value, command))
                conn.commit()
            filled += 1

    with db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            (BACKFILL_META_KEY, str(int(time.time()))))
        conn.commit()
    return {'filled': filled, 'skipped': False}


def start_backfill_thread():
    """Start the one-off backfill in the background. Returns the thread."""
    thread = threading.Thread(target=_run_backfill, name='inner-target-backfill',
                              daemon=True)
    thread.start()
    return thread


def _run_backfill():
    try:
        result = backfill_inner_targets()
        if not result['skipped']:
            print("[inner_target] backfilled %d commands" % result['filled'], flush=True)
    except Exception as exc:  # pragma: no cover - defensive: never break startup
        print("[inner_target] backfill failed: %s" % exc, flush=True)

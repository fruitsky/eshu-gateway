"""F18: fill `requests.command_sha` / `shape_sha` for rows that predate them.

Same shape as F7's inner-target backfill: batched by distinct command, one
`meta` completion key, daemon thread at startup so it never blocks anything.
Idempotent — only NULL rows are touched, and a second start short-circuits.
"""
import threading
import time

from db.core import db_conn

BACKFILL_META_KEY = 'request_fingerprint_backfill_done'
BATCH_SIZE = 500


def backfill_fingerprints(batch_size: int = BATCH_SIZE) -> dict:
    from core.fingerprint import fingerprints

    with db_conn() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?",
                           (BACKFILL_META_KEY,)).fetchone()
        if row:
            return {'filled': 0, 'skipped': True}

    filled = 0
    while True:
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT command FROM requests "
                "WHERE command_sha IS NULL LIMIT ?", (batch_size,)).fetchall()
        if not rows:
            break
        for row in rows:
            command = row[0]
            cmd_sha, shape_sha = fingerprints(command)
            with db_conn() as conn:
                conn.execute(
                    "UPDATE requests SET command_sha = ?, shape_sha = ? "
                    "WHERE command = ? AND command_sha IS NULL",
                    (cmd_sha, shape_sha, command))
                conn.commit()
            filled += 1

    with db_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                     (BACKFILL_META_KEY, str(int(time.time()))))
        conn.commit()
    return {'filled': filled, 'skipped': False}


def start_backfill_thread():
    thread = threading.Thread(target=_run, name='request-fingerprint-backfill',
                              daemon=True)
    thread.start()
    return thread


def _run():
    try:
        result = backfill_fingerprints()
        if not result['skipped']:
            print("[fingerprints] backfilled %d commands" % result['filled'],
                  flush=True)
    except Exception as exc:  # pragma: no cover - never break startup
        print("[fingerprints] backfill failed: %s" % exc, flush=True)

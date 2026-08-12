import sqlite3
import os
import random
import string
import time
from contextlib import contextmanager

DB_PATH = None

def get_db():
    global DB_PATH
    if DB_PATH is None:
        db_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        DB_PATH = os.path.join(db_dir, "eshu.db")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    # Wait up to 15s for the write lock instead of failing immediately under
    # transient contention (multiple pollers/UI threads writing at once).
    conn.execute("PRAGMA busy_timeout=15000")
    return conn

@contextmanager
def db_conn():
    """Context-managed connection that ALWAYS closes (and thus releases any
    write lock / rolls back uncommitted work) even when an exception occurs.
    Use this instead of manual get_db()/conn.close() so a leaked connection can
    never hold the SQLite write lock and wedge the whole dashboard."""
    conn = get_db()
    try:
        yield conn
    finally:
        conn.close()

def cursor_from(conn):
    return conn.cursor()

def init_db():
    conn = get_db()
    cursor = conn.cursor()

    from db.requests import init_requests_tables
    from db.gateways import init_gateways_tables
    from db.policies import init_policies_tables
    from db.enrollment import init_enrollment_tables
    from db.audit import init_audit_tables
    from db.auth import init_auth_tables
    from db.misc import init_misc_tables
    from db.windows import init_windows_tables
    from db.fleet import init_fleet_tables

    init_requests_tables(cursor)
    init_gateways_tables(cursor)
    init_policies_tables(cursor)
    init_enrollment_tables(cursor)
    init_audit_tables(cursor)
    init_auth_tables(cursor)
    init_misc_tables(cursor)
    init_windows_tables(cursor)
    init_fleet_tables(cursor)

    conn.commit()
    conn.close()

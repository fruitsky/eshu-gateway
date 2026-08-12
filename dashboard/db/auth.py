import hashlib
import os
from db.core import db_conn

def init_auth_tables(cursor):
    pass  # auth uses the meta table, seeded elsewhere

def get_password_hash():
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM meta WHERE key = 'dashboard_password'")
        row = cursor.fetchone()
        return row['value'] if row else ''

def set_password_hash(hash_value: str):
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('dashboard_password', ?)", (hash_value,))
        conn.commit()

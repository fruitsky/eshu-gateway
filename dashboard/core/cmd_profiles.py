"""Per-gateway command profiles for behavioural anomaly flags.

Builds a cached set of "base binaries" each gateway has run in a rolling
window, so a pending JIT can be flagged when a host is about to run something
it has never run before.

Mirrors the core/learning.py background-scanner pattern: rebuilds at startup +
periodically, read-only over the requests table.
"""
import time
import threading

from db.core import get_db

PROFILE_WINDOW_DAYS = 30
MIN_SAMPLES = 10        # don't flag a gateway until it has this many commands
REFRESH_INTERVAL = 3600

_LOCK = threading.Lock()
_profiles_cache = {
    'updated_at': 0,
    'gateways': {},     # ip -> {'base_commands': set, 'count': int}
}

PREFIXES = ['sudo ', 'nice ', 'nohup ', 'ionice ', 'env ', 'time ']


def base_command(cmd: str):
    """Normalize a command to its base binary: strip one leading common prefix
    (sudo/nice/nohup/ionice/env/time) and take the first whitespace token."""
    c = (cmd or '').strip()
    if not c:
        return None
    for p in PREFIXES:
        if c.startswith(p):
            c = c[len(p):].lstrip()
            break
    return c.split()[0] or None


def compute_profiles():
    cutoff = int(time.time()) - PROFILE_WINDOW_DAYS * 86400
    conn = get_db()
    cursor = conn.cursor()
    # Exclude in-flight 'pending' requests so a new command isn't counted as
    # "already seen" by the very request being evaluated.
    cursor.execute(
        "SELECT target_ip, command FROM requests WHERE created_at >= ? AND status != 'pending'",
        (cutoff,),
    )
    rows = cursor.fetchall()
    conn.close()

    gateways = {}
    for r in rows:
        base = base_command(r['command'])
        if not base:
            continue
        g = gateways.setdefault(r['target_ip'], {'base_commands': set(), 'count': 0})
        g['base_commands'].add(base)
        g['count'] += 1
    return {'updated_at': int(time.time()), 'gateways': gateways}


def refresh_profiles():
    global _profiles_cache
    try:
        with _LOCK:
            _profiles_cache = compute_profiles()
        return _profiles_cache
    except Exception:
        with _LOCK:
            return dict(_profiles_cache)


def get_anomaly(target_ip: str, command: str):
    """Return a human-readable anomaly reason if the command's base binary has
    never been seen on this gateway (and the gateway has enough history), else
    None."""
    base = base_command(command)
    if not base:
        return None
    with _LOCK:
        gateways = _profiles_cache['gateways']
    g = gateways.get(target_ip)
    if not g or g['count'] < MIN_SAMPLES:
        return None
    if base in g['base_commands']:
        return None
    return f"First time this gateway runs '{base}'"


def _profiles_loop():
    while True:
        time.sleep(REFRESH_INTERVAL)
        try:
            refresh_profiles()
        except Exception:
            pass

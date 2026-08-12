# Persistent gap scanner — analyzes the requests table across all gateways.
# Finds commands repeatedly approved via JIT that are not yet allowlisted.
# Runs in a background thread at startup + periodically. Read-only over requests.

import time
import threading
import re

from db.core import get_db
from db.gateways import get_gateways
from db.policies import get_policies
from db.misc import get_dismissed_policy_gaps, dismiss_policy_gap, get_seen_gaps, set_seen_gaps

MIN_APPROVALS = 3          # a command must be JIT-approved this many times
REFRESH_INTERVAL = 3600    # recompute every hour
_LOCK = threading.Lock()
_gaps_cache = {
    'updated_at': 0,
    'gateways': [],
    'total_gaps': 0,
    'new_gaps': 0,
}

def _is_allowlisted(cmd: str, exact_list, regex_list) -> bool:
    if cmd in exact_list:
        return True
    for pattern in regex_list:
        try:
            if re.search(pattern, cmd):
                return True
        except re.error:
            pass
    return False

def compute_gaps() -> dict:
    """Scan requests for repeated JIT approvals not yet allowlisted."""
    now = int(time.time())
    dismissed = get_dismissed_policy_gaps()
    seen = get_seen_gaps()
    policies = get_policies()
    exact_list = [e.strip() for e in (policies.get('exact_whitelist') or '').split('\n') if e.strip()]
    regex_list = [r.strip() for r in (policies.get('regex_whitelist') or '').split('\n') if r.strip()]

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT target_ip, command, COUNT(*) AS approved_count
        FROM requests
        WHERE status IN ('approved', 'consumed')
        GROUP BY target_ip, command
        HAVING approved_count >= ?
        ORDER BY approved_count DESC
    ''', (MIN_APPROVALS,))
    rows = cursor.fetchall()
    conn.close()

    # Build hostname lookup
    hostnames = {}
    for g in get_gateways():
        hostnames[g['ip']] = g.get('hostname') or g['ip']

    from core.cmd_descs import describe_command

    by_gw = {}
    total_gaps = 0
    new_gaps = 0
    for r in rows:
        cmd = r['command'].strip()
        ip = r['target_ip']
        if not cmd or _is_allowlisted(cmd, exact_list, regex_list):
            continue
        if cmd in dismissed:
            continue
        is_new = cmd not in (seen.get(ip) or {})
        if is_new:
            new_gaps += 1
        total_gaps += 1
        entry = {
            'command': cmd,
            'approved_count': r['approved_count'],
            'description': describe_command(cmd),
            'is_new': is_new,
        }
        by_gw.setdefault(ip, {'ip': ip, 'hostname': hostnames.get(ip, ip), 'gaps': []})
        by_gw[ip]['gaps'].append(entry)

    gateways = sorted(by_gw.values(), key=lambda g: max((x['approved_count'] for x in g['gaps']), default=0), reverse=True)
    return {
        'updated_at': now,
        'gateways': gateways,
        'total_gaps': total_gaps,
        'new_gaps': new_gaps,
    }

def refresh_gaps() -> dict:
    global _gaps_cache
    try:
        result = compute_gaps()
        with _LOCK:
            _gaps_cache = result
        return result
    except Exception:
        with _LOCK:
            return dict(_gaps_cache)

def get_cached_gaps() -> dict:
    if _gaps_cache['updated_at'] == 0:
        return refresh_gaps()
    with _LOCK:
        return dict(_gaps_cache)

def mark_all_seen():
    """Record the current set of gaps as seen and dismiss them so the screen clears."""
    result = compute_gaps()
    seen = get_seen_gaps()
    now = int(time.time())
    for gw in result.get('gateways', []):
        ip = gw['ip']
        seen.setdefault(ip, {})
        for gap in gw.get('gaps', []):
            seen[ip][gap['command']] = now
    set_seen_gaps(seen)
    for gw in result.get('gateways', []):
        for gap in gw.get('gaps', []):
            dismiss_policy_gap(gap['command'])
    refresh_gaps()

def _gaps_loop():
    while True:
        time.sleep(REFRESH_INTERVAL)
        try:
            refresh_gaps()
        except Exception:
            pass

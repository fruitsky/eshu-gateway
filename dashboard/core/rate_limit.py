import time
from fastapi import HTTPException

# Buckets are keyed by (kind, ip). A single dict keeps the
# `_rate_limit_buckets.clear()` reset in tests/conftest.py sufficient to wipe
# all limiter state between tests.
_rate_limit_buckets = {}
_RATE_LIMIT_MAX = 60
_RATE_LIMIT_WINDOW = 60

# Stricter bucket for credential guessing on /api/auth/login.
_LOGIN_RATE_LIMIT_MAX = 5
_LOGIN_RATE_LIMIT_WINDOW = 60

_LIMITS = {
    "general": (_RATE_LIMIT_MAX, _RATE_LIMIT_WINDOW),
    "login": (_LOGIN_RATE_LIMIT_MAX, _LOGIN_RATE_LIMIT_WINDOW),
}

_last_sweep = 0.0


def _sweep(now: float, window: int):
    """Drop IPs whose buckets have fully aged out, bounding dict growth."""
    global _last_sweep
    if now - _last_sweep < window:
        return
    _last_sweep = now
    for key in list(_rate_limit_buckets):
        fresh = [t for t in _rate_limit_buckets[key] if now - t < window]
        if fresh:
            _rate_limit_buckets[key] = fresh
        else:
            del _rate_limit_buckets[key]


def _check_rate_limit(ip: str, kind: str = "general"):
    max_hits, window = _LIMITS.get(kind, _LIMITS["general"])
    now = time.time()
    _sweep(now, window)
    key = (kind, ip)
    bucket = [t for t in _rate_limit_buckets.get(key, []) if now - t < window]
    if len(bucket) >= max_hits:
        raise HTTPException(status_code=429, detail="Too many requests. Slow down.")
    bucket.append(now)
    _rate_limit_buckets[key] = bucket


def check_agent_rate_limit_status(agent_id, max_hits, window: int = 60):
    """Per-agent bucket. Returns (allowed, retry_after_seconds).

    The MCP layer uses this to return an explicit `rate_limited` tool error
    instead of an HTTP 429. An HTTP 429 on /mcp makes some MCP clients park the
    server and register zero tools for the whole session, so the limit must
    never fire on the transport handshake/list calls.
    """
    try:
        agent_id = int(agent_id)
        max_hits = int(max_hits)
    except (TypeError, ValueError):
        return True, 0
    if max_hits <= 0:
        return True, 0
    now = time.time()
    _sweep(now, window)
    key = ("agent", agent_id)
    bucket = [t for t in _rate_limit_buckets.get(key, []) if now - t < window]
    if len(bucket) >= max_hits:
        # The oldest allowed call must age out before another is allowed.
        retry_after = max(1, int(window - (now - min(bucket))) + 1)
        _rate_limit_buckets[key] = bucket
        return False, retry_after
    bucket.append(now)
    _rate_limit_buckets[key] = bucket
    return True, 0


def check_agent_rate_limit(agent_id, max_hits, window: int = 60) -> bool:
    """Boolean wrapper kept for callers/tests that only need allow/deny."""
    allowed, _retry_after = check_agent_rate_limit_status(agent_id, max_hits, window)
    return allowed

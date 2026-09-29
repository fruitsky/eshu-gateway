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

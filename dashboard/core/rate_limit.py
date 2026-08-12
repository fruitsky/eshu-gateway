import time
from fastapi import HTTPException

_rate_limit_buckets = {}
_RATE_LIMIT_MAX = 60
_RATE_LIMIT_WINDOW = 60

def _check_rate_limit(ip: str):
    now = time.time()
    bucket = _rate_limit_buckets.get(ip, [])
    bucket = [t for t in bucket if now - t < _RATE_LIMIT_WINDOW]
    if len(bucket) >= _RATE_LIMIT_MAX:
        raise HTTPException(status_code=429, detail="Too many requests. Slow down.")
    bucket.append(now)
    _rate_limit_buckets[ip] = bucket

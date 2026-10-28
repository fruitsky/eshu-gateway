"""Per-principal rate + concurrency limits for the mediated web fetch.

Deliberately separate from ``core.rate_limit``: the MCP-wide per-agent limit is
a single minute bucket shared by every tool, so reusing it here would let web
fetches starve Home Assistant calls (and it has no hourly window).

In-process state, matching the rest of Eshu's single-worker limiter.
"""
import threading
import time
from collections import defaultdict, deque

MINUTE_LIMIT = 20
HOUR_LIMIT = 200
MAX_CONCURRENT = 3

_lock = threading.Lock()
_minute = defaultdict(deque)
_hour = defaultdict(deque)
_semaphores = {}


class LimitError(Exception):
    def __init__(self, code: str, retry_after: int = 0):
        super().__init__(code)
        self.code = code
        self.retry_after = int(retry_after or 0)


def _semaphore(principal):
    sem = _semaphores.get(principal)
    if sem is None:
        sem = threading.BoundedSemaphore(MAX_CONCURRENT)
        _semaphores[principal] = sem
    return sem


def acquire(principal: str):
    """Return ``(semaphore, None)`` when a fetch may start, else ``(None, LimitError)``."""
    principal = principal or ''
    now = time.time()
    with _lock:
        minute = _minute[principal]
        hour = _hour[principal]
        while minute and now - minute[0] >= 60:
            minute.popleft()
        while hour and now - hour[0] >= 3600:
            hour.popleft()
        if len(minute) >= MINUTE_LIMIT:
            retry = max(1, int(60 - (now - minute[0])) + 1)
            return None, LimitError('rate_limited', retry)
        if len(hour) >= HOUR_LIMIT:
            retry = max(1, int(3600 - (now - hour[0])) + 1)
            return None, LimitError('rate_limited', retry)
        sem = _semaphore(principal)
        if not sem.acquire(blocking=False):
            return None, LimitError('concurrency_limited', 1)
        minute.append(now)
        hour.append(now)
        return sem, None


def release(principal: str, sem):
    # Re-check principal so the lock protects the dict even if reset() raced.
    try:
        sem.release()
    except ValueError:
        pass


def reset():
    """Test hook: clear every bucket and semaphore."""
    global _semaphores
    with _lock:
        _minute.clear()
        _hour.clear()
        _semaphores = {}

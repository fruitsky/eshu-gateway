import hmac
import hashlib
import secrets
import time
from fastapi import Request, HTTPException

SESSION_KEY = secrets.token_hex(32)
SESSION_TTL = 24 * 60 * 60

def _make_session_token() -> str:
    now = int(time.time())
    payload = f"{now}:{secrets.token_hex(16)}"
    sig = hmac.new(SESSION_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"

def _verify_session_token(token: str) -> bool:
    try:
        payload, sig = token.rsplit(".", 1)
        expected = hmac.new(SESSION_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return False
        ts = int(payload.split(":", 1)[0])
        return time.time() - ts < SESSION_TTL
    except Exception:
        return False

def _check_session(request: Request):
    # Fail closed: protected endpoints always require a valid session cookie,
    # regardless of whether a password is currently set. The only unauth'd path
    # into a first-run dashboard is the setup overlay (POST /api/auth/set-password).
    if not _check_session_optional(request):
        raise HTTPException(status_code=401, detail="Not authenticated — login required")

def _check_session_optional(request: Request) -> bool:
    cookie = request.cookies.get("eshu_session")
    return bool(cookie and _verify_session_token(cookie))

def _is_password_protected():
    from db.auth import get_password_hash
    pw = get_password_hash()
    return bool(pw)

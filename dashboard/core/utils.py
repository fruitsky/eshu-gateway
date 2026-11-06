"""Shared utility functions used by multiple route modules."""
import base64
import hashlib
import hmac
import re
import secrets
import os
import time
from fastapi import Request, HTTPException
from db.gateways import get_gateway_by_token

# ── Version ──
DASHBOARD_VERSION = "v0.1.0"

# ── Gateway metadata charsets ──
# Mirrors the prefix validation in dashboard/eshu-gateway.sh. A gateway
# concatenates these values into its JSON bodies, so an old gateway can still
# forward an injected value; the dashboard rejects anything outside the charset.
SESSION_ID_PATTERN = r'^[A-Za-z0-9_-]{1,64}$'
SESSION_SIG_PATTERN = r'^[0-9a-f]{64}$'
EXECUTION_ID_PATTERN = r'^[A-Za-z0-9_.:-]{1,64}$'
WINDOW_TOKEN_PATTERN = r'^[A-Za-z0-9]{1,64}$'

_SESSION_ID_RE = re.compile(SESSION_ID_PATTERN)
_EXECUTION_ID_RE = re.compile(EXECUTION_ID_PATTERN)


def valid_gateway_metadata(session_id: str, execution_id: str) -> None:
    """Raise 400 when a *set* session/execution id is outside the allowed charset.

    Empty values stay valid for compatibility (most gateways send no prefix).
    """
    if session_id and not _SESSION_ID_RE.fullmatch(session_id):
        raise HTTPException(status_code=400, detail="Invalid session_id")
    if execution_id and not _EXECUTION_ID_RE.fullmatch(execution_id):
        raise HTTPException(status_code=400, detail="Invalid execution_id")

# ── Token decode ──
def decode_cmd(encoded: str) -> str:
    try:
        return base64.b64decode(encoded).decode('utf-8')
    except Exception:
        return encoded

# ── Gateway token resolution ──
def _resolve_gateway_token(request: Request):
    token = request.headers.get('X-Gateway-Token', '').strip()
    if not token or token == 'None':
        return None, None
    gw = get_gateway_by_token(token)
    if not gw:
        raise HTTPException(status_code=401, detail="Invalid gateway token. Re-enroll this gateway.")
    return gw['ip'], gw.get('hostname', '')

# ── Password hashing (PBKDF2-SHA256, stdlib only) ──
def _hash_password(password: str, salt: str = None) -> str:
    if salt is None:
        salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'),
                                salt.encode('utf-8'), 200_000, dklen=32)
    return f"$pbkdf2${salt}${key.hex()}"

def _verify_password(password: str, stored: str) -> bool:
    try:
        parts = stored.split('$')
        if len(parts) != 4 or parts[1] != 'pbkdf2':
            return password == stored
        _, _, salt, key_hex = parts
        expected = _hash_password(password, salt)
        return hmac.compare_digest(expected, stored)
    except Exception:
        return False

# ── SSH key identity ──
GW_COLORS = ['#e63946','#f4a261','#2a9d8f','#e76f51','#264653','#dda15e','#606c38','#9c89b8','#e07a5f','#81b29a']
GW_LABELS = ['Core 1','Core 2','Edge 1','Edge 2','Worker','DMZ','DB','Monitoring','CI/CD','Backup']

def derive_gateway_identity(hostname: str):
    import hashlib
    idx = int(hashlib.md5(hostname.encode()).hexdigest(), 16) % len(GW_COLORS)
    return GW_COLORS[idx], GW_LABELS[idx]

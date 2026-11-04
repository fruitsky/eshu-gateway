"""HMAC-sign a session id so a gateway can verify it was minted here.

The dashboard has one signing key (32 random bytes, hex, stored in `meta`).
It is sent to authenticated gateways in the policy response and written to
/etc/eshu-session-key. `session_begin` returns the id plus its signature; the
gateway verifies the pair locally, with no per-session sync and no round-trip.

This is an audit guarantee ("this id was minted by this dashboard"), not an
authorization decision: the agent already holds the token.
"""
import hashlib
import hmac

from db.misc import get_previous_session_signing_key, get_session_signing_key


def sign_session(session_id: str) -> str:
    key = get_session_signing_key()
    return hmac.new(bytes.fromhex(key), (session_id or '').encode(),
                    hashlib.sha256).hexdigest()


def verify_session(session_id: str, signature: str, keys=None) -> bool:
    sid = (session_id or '').strip()
    sig = (signature or '').strip()
    if not sid or not sig:
        return False
    if keys is None:
        keys = [get_session_signing_key(), get_previous_session_signing_key()]
    for key in keys:
        if not key:
            continue
        try:
            expected = hmac.new(bytes.fromhex(key), sid.encode(),
                                hashlib.sha256).hexdigest()
        except ValueError:
            continue
        if hmac.compare_digest(expected, sig):
            return True
    return False

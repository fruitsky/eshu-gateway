"""Always-on output secret scrubbing for the integration proxy.

Generic passthrough tools return upstream payloads untouched, which lets
controller secrets through (e.g. Omada's SSID `pskSetting.securityKey`).
This module applies an always-on scrub to every proxied response body and to
audit-visible payloads so secret-named fields are masked regardless of which
tool produced the data.

Deliberately conservative: a value is masked when its KEY names a secret field
(plus a small set of high-confidence header/JWT value patterns). A blanket
"long hex/base64 >= 16 chars" rule is deliberately NOT used — it would destroy
legitimate UUIDs, site ids, and resource ids that these APIs are full of.
"""
import hashlib
import json
import re

# Exact secret field names (compared lowercased).
_SECRET_KEYS = {
    'password', 'passphrase', 'secret', 'psk', 'securitykey', 'clientsecret',
    'apikey', 'api_key', 'apitoken', 'api_token', 'accesskey', 'authkey',
    'privatekey', 'private_key', 'authorization', 'cookie',
}

# Suffixes that mark a field as secret-bearing regardless of prefix (covers
# accessToken, refreshToken, sessionToken, ...). Deliberately does NOT include
# a bare 'key' suffix (searchKey, monkey, ... are common non-secrets).
_SECRET_SUFFIXES = ('token', 'secret', 'password', 'passphrase', 'psk',
                    'authorization', 'cookie')

# High-confidence value patterns applied to any string, regardless of key.
_VALUE_PATTERNS = [
    re.compile(r'Bearer\s+[A-Za-z0-9\-._~+/]+=*', re.IGNORECASE),
    re.compile(r'AccessToken=[A-Za-z0-9\-._~+/]+=*'),
    re.compile(r'PVEAPIToken=[^\s"]+'),
    re.compile(r'eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}'),
]

MASK = '[redacted]'


def _is_secret_key(key: str) -> bool:
    k = str(key).lower()
    return k in _SECRET_KEYS or k.endswith(_SECRET_SUFFIXES)


def _scrub_string(value: str) -> str:
    for pattern in _VALUE_PATTERNS:
        value = pattern.sub(MASK, value)
    return value


def scrub_string(value: str) -> str:
    """Public wrapper: mask high-confidence token patterns in a flat string
    (e.g. an error message). Non-strings pass through unchanged."""
    if isinstance(value, str):
        return _scrub_string(value)
    return value


def secret_hashes(value, prefix: str = '') -> dict:
    """Walk a payload and return {dotted.path: sha256-hex} for every leaf value
    under a secret-named key. Used to fingerprint credentials in the audit
    trail without retaining them."""
    out = {}
    if isinstance(value, dict):
        for k, v in value.items():
            path = f'{prefix}.{k}' if prefix else str(k)
            if _is_secret_key(k):
                if isinstance(v, str) and v:
                    out[path] = hashlib.sha256(v.encode('utf-8')).hexdigest()
                elif isinstance(v, (int, float, bool)) and v is not False and v != 0:
                    out[path] = hashlib.sha256(str(v).encode('utf-8')).hexdigest()
            else:
                out.update(secret_hashes(v, path))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            out.update(secret_hashes(v, f'{prefix}[{i}]'))
    return out


def scrub_value(value):
    """Recursively scrub a parsed JSON value (dict/list/scalar)."""
    if isinstance(value, dict):
        return {k: (MASK if _is_secret_key(k) else scrub_value(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_value(v) for v in value]
    if isinstance(value, str):
        return _scrub_string(value)
    return value


def scrub_body(body: str) -> str:
    """Scrub a response body string. JSON bodies get the recursive key/value
    scrub; non-JSON bodies still get the high-confidence value-pattern pass."""
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return _scrub_string(body)
    return json.dumps(scrub_value(data))


def scrub_payload(payload) -> dict:
    """Scrub an args/payload dict for audit/UI display. Returns a new dict."""
    out = scrub_value(payload or {})
    return out if isinstance(out, dict) else dict(payload or {})

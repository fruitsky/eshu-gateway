"""URL parsing, allowlist matching and validation for the mediated web fetch.

Pure functions only — no DNS, no network. Keeping this separate makes the
security boundary table-testable, and guarantees a denial happens before any
resolver call (requirement S1).
"""
import ipaddress
import re
import urllib.parse
from dataclasses import dataclass

MAX_URL_CHARS = 2048
ALLOWED_SCHEMES = {'http', 'https'}
ALLOWED_PORTS = {80, 443}

# Any ASCII whitespace/control character is a header-injection / smuggling risk.
_CONTROL_RE = re.compile(r'[\x00-\x20\x7f]')
_UNICODE_DOTS = str.maketrans({'\u3002': '.', '\uff0e': '.', '\uff61': '.'})

# A domain entry / URL host: at least two labels, each label starts and ends
# with an alphanumeric, hyphens only in the middle, no wildcard or port.
_HOST_RE = re.compile(
    r'^(?=.{1,253}$)'
    r'([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)'
    r'(\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$'
)


class UrlError(ValueError):
    """A URL was refused. Carries a stable machine code and final/friendly reason."""

    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


@dataclass(frozen=True)
class ParsedURL:
    scheme: str
    host: str
    port: int
    path: str
    query: str
    url: str


@dataclass(frozen=True)
class Decision:
    allowed: bool
    code: str
    reason: str
    matched: str
    parsed: ParsedURL = None


def _idna(host: str) -> str:
    try:
        return host.encode('idna').decode('ascii')
    except (UnicodeError, UnicodeDecodeError) as e:
        raise UrlError('invalid_url', 'host is not a valid IDNA name') from e


def _clean_host(raw: str) -> str:
    """Lowercase, strip trailing dots, map Unicode dots, IDNA-encode."""
    host = (raw or '').translate(_UNICODE_DOTS).strip().lower()
    while host.endswith('.'):
        host = host[:-1]
    if not host:
        raise UrlError('invalid_url', 'URL has no host')
    if '%' in host or '\\' in host or _CONTROL_RE.search(host):
        raise UrlError('invalid_url', 'host contains invalid characters')
    return _idna(host)


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _validate_hostname(host: str, original: str) -> str:
    if _is_ip_literal(host):
        raise UrlError('host_not_allowed', f'IP literals are not allowed ({original})')
    if not _HOST_RE.match(host):
        raise UrlError('host_not_allowed', f'host is not a valid domain ({original})')
    if host.rsplit('.', 1)[-1].isdigit():
        raise UrlError('host_not_allowed', f'host has a numeric TLD ({original})')
    return host


def parse_url(url: str) -> ParsedURL:
    """Validate and normalise an absolute http(s) URL. No resolution is done."""
    if not isinstance(url, str):
        raise UrlError('invalid_url', 'URL must be a string')
    raw = url.strip()
    if not raw:
        raise UrlError('invalid_url', 'URL is empty')
    if len(raw) > MAX_URL_CHARS:
        raise UrlError('invalid_url', 'URL is too long')
    if _CONTROL_RE.search(raw) or '\\' in raw:
        raise UrlError('invalid_url', 'URL contains whitespace or control characters')

    try:
        parsed = urllib.parse.urlsplit(raw)
        scheme = (parsed.scheme or '').lower()
        port = parsed.port
    except ValueError as e:
        raise UrlError('invalid_url', 'URL could not be parsed') from e

    if scheme not in ALLOWED_SCHEMES:
        raise UrlError('scheme_not_allowed', f'scheme not allowed ({scheme or "none"})')
    if parsed.username is not None or parsed.password is not None or '@' in parsed.netloc:
        raise UrlError('invalid_url', 'URLs with credentials are not allowed')
    if parsed.hostname is None:
        raise UrlError('invalid_url', 'URL has no host')

    host = _clean_host(parsed.hostname)
    host = _validate_hostname(host, parsed.hostname)
    if port is None:
        port = 443 if scheme == 'https' else 80
    if port not in ALLOWED_PORTS:
        raise UrlError('port_not_allowed', f'port not allowed ({port})')

    default_port = 443 if scheme == 'https' else 80
    netloc = host if port == default_port else f'{host}:{port}'
    path = parsed.path or '/'
    query = parsed.query or ''
    normalised = urllib.parse.urlunsplit((scheme, netloc, path, query, ''))
    return ParsedURL(scheme, host, port, path, query, normalised)


def normalize_entry(entry: str) -> str:
    """Validate and normalise one allow/deny entry. Raises ValueError on a bad entry."""
    if not isinstance(entry, str):
        raise ValueError('entries must be strings')
    raw = entry.strip()
    if not raw:
        raise ValueError('entries must not be empty')
    if any(c in raw for c in ('*', '/', '\\', '@', ':', '?', '#', ' ')):
        raise ValueError(f'invalid allowlist entry {entry!r}: wildcards, paths, ports and userinfo are not allowed')
    if _CONTROL_RE.search(raw):
        raise ValueError(f'invalid allowlist entry {entry!r}: control characters are not allowed')
    host = raw.translate(_UNICODE_DOTS).lower()
    while host.endswith('.'):
        host = host[:-1]
    if not host:
        raise ValueError('entries must not be empty')
    try:
        host = _idna(host)
    except UrlError as e:
        raise ValueError(f'invalid allowlist entry {entry!r}: {e.reason}') from e
    if _is_ip_literal(host):
        raise ValueError(f'invalid allowlist entry {entry!r}: IP literals are not allowed')
    if not _HOST_RE.match(host):
        raise ValueError(f'invalid allowlist entry {entry!r}: must be a domain with at least two labels')
    if host.rsplit('.', 1)[-1].isdigit():
        raise ValueError(f'invalid allowlist entry {entry!r}: numeric TLDs are not allowed')
    return host


def validate_policy(allow, deny):
    """Normalise/dedupe an allow/deny pair. Raises ValueError with a user message."""
    allow = allow or []
    deny = deny or []
    if not isinstance(allow, (list, tuple)):
        raise ValueError('allow must be a list of domains')
    if not isinstance(deny, (list, tuple)):
        raise ValueError('deny must be a list of domains')
    clean_allow = sorted({normalize_entry(e) for e in allow})
    clean_deny = sorted({normalize_entry(e) for e in deny})
    return clean_allow, clean_deny


def host_matches(host: str, entry: str) -> bool:
    """Label-boundary suffix match. Never a substring match."""
    return host == entry or host.endswith('.' + entry)


def evaluate(policy: dict, url: str) -> Decision:
    """Decide whether `url` is permitted. Evaluated before any DNS lookup."""
    policy = policy or {}
    if not policy.get('enabled'):
        return Decision(False, 'disabled', 'web_fetch_url is disabled', '')
    try:
        parsed = parse_url(url)
    except UrlError as e:
        return Decision(False, e.code, e.reason, '')
    try:
        allow = [normalize_entry(e) for e in (policy.get('allow') or [])]
        deny = [normalize_entry(e) for e in (policy.get('deny') or [])]
    except ValueError as e:
        # A malformed entry should have been rejected at write time; fail closed.
        return Decision(False, 'config_error', f'invalid web_fetch policy: {e}', '')

    # Deny is evaluated before allow (hard override).
    for entry in deny:
        if host_matches(parsed.host, entry):
            return Decision(False, 'host_denied',
                            f'host is denied by policy ({parsed.host})', entry, parsed)
    if not allow:
        return Decision(False, 'host_not_allowed',
                        f'host not allowlisted ({parsed.host})', '', parsed)
    for entry in allow:
        if host_matches(parsed.host, entry):
            return Decision(True, 'ok', '', entry, parsed)
    return Decision(False, 'host_not_allowed',
                    f'host not allowlisted ({parsed.host})', '', parsed)

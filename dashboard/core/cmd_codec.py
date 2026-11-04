"""Detect and preview encoded command execution.

The gateway blocks/allows on the literal command string. An encoded payload
(`echo <base64> | base64 -d | bash`, `eval`, `bash -c "$(...)"`, …) hides the
real command from the blocklist and from the operator, so this module:

  * detects decode-to-exec wrappers,
  * extracts a bounded decoded preview for the approval UI,
  * checks the decoded text against the hard/central blocklists.

It never executes anything. The preview is best-effort: nested encoding and
`eval` of a runtime variable can still hide intent, so encoded execution is
treated as high risk and forced to JIT rather than auto-approved.
"""
import base64
import binascii
import re

MAX_PREVIEW = 8192
# A decoded script should be mostly printable; otherwise we are probably
# looking at a hash/certificate blob rather than a command.
MIN_PRINTABLE = 0.80

ENCODED_EXEC_PATTERNS = [
    r'base64\s+(?:-d|--decode|-D)\b',
    r'xxd\s+-r\b',
    r'openssl\s+enc\s+-d\b',
    r'gzip\s+-d\b',
    r'\beval\b',
    r'\b(?:ba)?sh\s+-c\s+["\']?\$\(',
    r'\bpython[0-9.]*\s+-c\b.*\b(?:exec|eval)\s*\(',
    r'\bperl\s+-e\b',
]
_ENCODED_EXEC = re.compile('|'.join(ENCODED_EXEC_PATTERNS), re.IGNORECASE)
_BASE64_BLOB = re.compile(r'[A-Za-z0-9+/]{16,}={0,2}')


def detect_encoded_exec(command: str) -> str:
    """Return the matched wrapper fragment, or '' when none is present."""
    if not command:
        return ''
    match = _ENCODED_EXEC.search(command)
    return match.group(0).strip() if match else ''


def _decode_base64(blob: str) -> bytes:
    try:
        padded = blob + '=' * (-len(blob) % 4)
        return base64.b64decode(padded, validate=False)
    except (binascii.Error, ValueError):
        return b''


def _printable_ratio(text: str) -> float:
    if not text:
        return 0.0
    good = sum(1 for ch in text if ch.isprintable() or ch in '\n\r\t')
    return good / len(text)


def decode_preview(command: str, max_bytes: int = MAX_PREVIEW):
    """Return {'kind','text','truncated'} for an encoded wrapper, or None.

    Only base64 wrappers are decoded (the form the agents use for multi-line
    scripts). Other detectors still force JIT, they just have no preview.
    """
    if not detect_encoded_exec(command):
        return None
    blobs = _BASE64_BLOB.findall(command or '')
    if not blobs:
        return None
    # Prefer the longest blob: the payload is always much longer than a hash
    # or a path fragment that happens to look base64-ish.
    for blob in sorted(set(blobs), key=len, reverse=True)[:4]:
        raw = _decode_base64(blob)
        if not raw:
            continue
        text = raw[:max_bytes].decode('utf-8', 'replace')
        if not text or _printable_ratio(text) < MIN_PRINTABLE:
            continue
        return {
            'kind': 'base64',
            'text': text,
            'truncated': len(raw) > max_bytes,
        }
    return None


def scan_decoded(text: str, hard_patterns=(), blocklist_patterns=()):
    """Return the blocklist pattern matched by the decoded text, or None.

    Mirrors the gateway's substring semantics: skip comments, strip one leading
    '^' and one trailing '$' from central blocklist entries.
    """
    if not text:
        return None
    for pattern in hard_patterns:
        if pattern and pattern in text:
            return pattern
    for pattern in blocklist_patterns:
        candidate = (pattern or '').strip()
        if not candidate or candidate.startswith('#'):
            continue
        if candidate.startswith('^'):
            candidate = candidate[1:]
        if candidate.endswith('$'):
            candidate = candidate[:-1]
        if candidate and candidate in text:
            return candidate
    return None

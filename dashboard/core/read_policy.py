"""One notion of "read" for auto-run decisions (§ 2.6a).

Chunk H owns the integration definition: `_read_only_refusal` for HTTP tools
(GET/HEAD only) and `is_read_command` for Home Assistant WebSocket commands.
F4 owns the SSH one. Everything that proposes to auto-run a read calls **this
module** — never the underlying checks directly — so there is exactly one
answer to "is this a read?" on each surface.

`is_read_ssh` is the SSH rule set; it only looks at F1's breakdown and never at
raw shell text. It fails closed: anything it cannot prove is a read is not one.
"""
from core.ha_ws_policy import is_read_command
from core.tool_runner import _read_only_refusal

# Flags that make a command ineligible on sight (F1 sets them).
READ_BLOCKING_FLAGS = frozenset({
    'encoded',           # payload hidden in base64
    'heredoc',           # body the parser cannot see
    'nested-ssh',        # a second machine we cannot see
    'sensitive-path',    # .env, keys, shadow, tokens, ...
    'redirect-to-file',  # writes a file (or /dev/sdX)
})

# A root read may only touch these trees: they cannot hold user secrets.
ELEVATED_SAFE_PREFIXES = ('/proc/', '/sys/', '/var/log/')
ELEVATED_SAFE_EXACT = ('/etc/os-release',)

_PATH_STARTS = ('/', './', '../', '~')


def is_read_integration_call(tool, transport, args):
    """(ok, reason) — a thin wrapper over Chunk H, no new logic."""
    refusal = _read_only_refusal(tool, transport, args)
    if refusal:
        return False, refusal[0]
    return True, ''


def _path_tokens(text):
    """Path-looking tokens in one step's text (quotes/commas trimmed)."""
    for raw in (text or '').split():
        token = raw.strip('\'"`,;()[]')
        if token.startswith(_PATH_STARTS):
            yield token


def _elevated_path_allowed(token):
    token = token.split('*', 1)[0]
    # A `..` anywhere makes the textual prefix meaningless (`/var/log/../etc`).
    if '..' in token.split('/'):
        return False
    if token in ELEVATED_SAFE_EXACT:
        return True
    if any(token == prefix.rstrip('/') for prefix in ELEVATED_SAFE_PREFIXES):
        return True
    return any(token.startswith(prefix) for prefix in ELEVATED_SAFE_PREFIXES)


def is_read_ssh(breakdown):
    """(ok, reason) for a command that should run without a prompt.

    Eligible only if F1 fully parsed it, every step reads, no blocking flag is
    set, and no elevated step touches a path outside the safe trees.
    """
    bd = breakdown or {}
    if not bd.get('complete'):
        return False, 'incomplete'
    steps = bd.get('steps') or []
    if not steps:
        return False, 'no-step'
    flags = set(bd.get('flags') or [])
    blocking = flags & READ_BLOCKING_FLAGS
    if blocking:
        return False, 'flag:' + ','.join(sorted(blocking))
    for step in steps:
        if (step.get('kind') or 'unknown') != 'read':
            return False, 'not-read'
        if not step.get('elevated'):
            continue
        for token in _path_tokens(step.get('text')):
            if not _elevated_path_allowed(token):
                return False, 'elevated-path:' + token
    return True, ''

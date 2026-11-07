"""Server-side hard/catastrophic blocklist for approval-time validation.

Mirrors the gateway Stage 1 blocklist (eshu-gateway.sh) and the client-side
isHardcoreBlocked(). Two layers:

- SELF_PROTECTION_PATTERNS — guard the Eshu install itself. Never editable.
- EVASION_PATTERNS        — anti-tamper command substitution. Never editable.
- CORE_COMMAND_PATTERNS   — shipped command-safety patterns. Editable via the
  normal blocklist (seeded by default on first run); enforced through the
  blocklist file so a human can relax them from the dashboard.
"""

# Non-editable — guard the gateway install itself. Paths are normalised
# (repeated '/' collapsed, '/./' removed) before matching, mirroring the
# gateway's stage-1 blocklist.
SELF_PROTECTION_PATTERNS = [
    '/usr/local/bin/eshu-', '/etc/eshu', '/var/run/eshu', '/run/eshu',
    'eshu.db', 'eshu.tickets', 'eshu-session-key', 'eshu/features.d',
    '/etc/sudoers.d/eshu',
]

# Non-editable — anti-tamper command substitution evasion
EVASION_PATTERNS = [
    '$(which ', '`which ',
]

# Editable, seeded into the blocklist (regex_blacklist) on first run
CORE_COMMAND_PATTERNS = [
    # Recursive force delete
    'rm -rf', 'rm  -rf', 'rm   -rf', 'rm -fr', 'rm -r -f', 'rm -f -r',
    '/bin/rm -rf', '/bin/rm -fr',
    # Filesystem format
    'mkfs',
    # Raw disk access
    'dd if=', 'dd  if=', 'dd of=', '/bin/dd',
    # Firewall flush
    'iptables -F', 'iptables --flush', 'iptables -X', 'iptables --delete-chain',
    'ip6tables -F', 'ip6tables --flush', 'ip6tables -X',
    'nft flush',
    # Power control
    'reboot', 'shutdown', 'poweroff', 'halt',
    'init 0', 'init 6', 'telinit 0', 'telinit 6',
    'systemctl reboot', 'systemctl poweroff', 'systemctl halt',
    'systemctl isolate reboot', 'systemctl isolate poweroff', 'systemctl isolate halt',
    'busybox reboot', 'busybox poweroff', 'busybox halt', 'busybox shutdown',
]

HARD_PATTERNS = SELF_PROTECTION_PATTERNS + EVASION_PATTERNS

# Chunk 2a: metacharacter commands never auto-approve; they go to JIT.
SHELL_METACHARACTERS = (';', '&', '|', '`', '$(', '>', '<', '\n', '\r', '\\')

GLOB_CHARACTERS = ('*', '?', '[')


def normalize_command_paths(cmd: str) -> str:
    """Collapse repeated '/' and remove '/./' segments before hard matching."""
    value = cmd or ''
    while '//' in value:
        value = value.replace('//', '/')
    while '/./' in value:
        value = value.replace('/./', '/')
    return value


def has_shell_metacharacters(cmd: str) -> bool:
    """True when the command contains any character the auto-run gate rejects."""
    value = cmd or ''
    return any(token in value for token in SHELL_METACHARACTERS)


def hard_block_match(cmd: str):
    """Return the matched pattern (or None) if the command hits the FATAL
    hardcoded blocklist: explicit Eshu paths/tokens + evasion only. Mirrors
    dashboard/eshu-gateway.sh stage 1 after path normalisation."""
    if not cmd:
        return None
    normalized = normalize_command_paths(cmd)
    for pattern in HARD_PATTERNS:
        if pattern in normalized:
            return pattern
    return None


def self_protection_jit_match(cmd: str):
    """Return the force-JIT self-protection token (or None).

    These broad matches must never be FATAL (a false positive would be
    permanent) and must never auto-run: any user's authorized_keys, or a
    wildcard next to an ``esh`` path component. Mirrors stage 1.5 of
    dashboard/eshu-gateway.sh.
    """
    if not cmd:
        return None
    normalized = normalize_command_paths(cmd)
    if '.ssh/authorized_keys' in normalized:
        return '.ssh/authorized_keys'
    if not any(ch in normalized for ch in GLOB_CHARACTERS):
        return None
    lower = normalized.lower()
    if (lower.startswith('esh') or '/esh' in lower
            or ' esh' in lower or '=esh' in lower):
        return 'glob+esh'
    return None


def blocklist_substring_match(pattern: str, cmd: str) -> bool:
    """Mirror the gateway's blocklist semantics: literal substring match with
    optional leading '^' / trailing '$' anchors stripped, '#' comment lines
    skipped. The gateway reads /etc/eshu-rblack.txt with
    `[[ "$cmd" == *"$p"* ]]` after stripping anchors; the dashboard tester and
    dispatch checks must agree with what the gateway actually enforces."""
    p = (pattern or '').strip()
    if not p or p.startswith('#'):
        return False
    if p.startswith('^'):
        p = p[1:]
    if p.endswith('$'):
        p = p[:-1]
    return p in cmd

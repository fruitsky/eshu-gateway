"""Server-side hard/catastrophic blocklist for approval-time validation.

Mirrors the gateway Stage 1 blocklist (eshu-gateway.sh) and the client-side
isHardcoreBlocked(). Two layers:

- SELF_PROTECTION_PATTERNS — guard the Eshu install itself. Never editable.
- EVASION_PATTERNS        — anti-tamper command substitution. Never editable.
- CORE_COMMAND_PATTERNS   — shipped command-safety patterns. Editable via the
  normal blocklist (seeded by default on first run); enforced through the
  blocklist file so a human can relax them from the dashboard.
"""

# Non-editable — guard the gateway install itself
SELF_PROTECTION_PATTERNS = [
    '/usr/local/bin/eshu-', '/etc/eshu-', '/var/run/eshu.',
    'eshu.db', 'eshu.db-journal', 'eshu.db-wal',
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


def hard_block_match(cmd: str):
    """Return the matched pattern (or None) if the command hits the hardcoded
    (non-editable) blocklist: self-protection + evasion only."""
    if not cmd:
        return None
    for pattern in HARD_PATTERNS:
        if pattern in cmd:
            return pattern
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

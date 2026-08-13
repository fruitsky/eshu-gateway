"""Server-side hardcoded catastrophic blocklist for Fleet Run approval-time
validation. Mirrors the gateway Stage 1 blocklist (eshu-gateway.sh) and the
client-side HARDCORE_BLOCKED_PATTERNS (app.js). These cannot be overridden."""

HARD_BLOCK_PATTERNS = [
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
    # Eshu self-access
    '/usr/local/bin/eshu-', '/etc/eshu-', '/var/run/eshu.',
    # Evasion
    '$(which ', '`which ',
]


def hard_block_match(cmd: str):
    """Return the matched pattern (or None) if the command hits the hardcoded
    catastrophic blocklist."""
    if not cmd:
        return None
    for pattern in HARD_BLOCK_PATTERNS:
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

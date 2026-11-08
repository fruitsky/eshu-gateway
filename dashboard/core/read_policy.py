"""One notion of "read" for auto-run decisions (§ 2.6a, F4).

Chunk H owns the integration definition: `_read_only_refusal` for HTTP tools
(GET/HEAD only) and `is_read_command` for Home Assistant WebSocket commands.
F4 owns the SSH one. Everything that proposes to auto-run a read calls **this
module** — never the underlying checks directly.

The SSH rule is a closed allowlist on top of F1's breakdown (review round 1,
R1): the gateway runs *every* command as root, so "elevated" is meaningless
and a plain `cat` reaches everything. A command is eligible only when F1
parsed it completely, every step is one of a small reviewed set of read verbs,
no flag is set (R3), every path operand is inside the safe trees (R2), and
there is no inner target (R4). Anything not proven is refused.
"""
import re

from core.ha_ws_policy import is_read_command
from core.tool_runner import _read_only_refusal

# R3: any F1 flag blocks unless it is on this harmless list — deliberately
# empty, so `repo-config`, `follows` and any future flag fail closed.
READ_HARMLESS_FLAGS = frozenset()

# R1: the reviewed verbs. The first group takes no file operands (only fixed
# status subcommands); the second may only read paths inside the safe trees.
READ_LANE_VERBS = {
    'status': frozenset({
        'uptime', 'df', 'free', 'ss', 'ps', 'uname', 'hostname', 'lsblk',
        'lscpu', 'whoami', 'id', 'date', 'nproc', 'ip', 'systemctl',
        'journalctl', 'docker', 'pct', 'qm', 'zpool', 'zfs',
    }),
    'file': frozenset({'cat', 'head', 'tail', 'grep', 'wc', 'ls', 'stat', 'du'}),
}

# R2: what a file reader may touch. Every step is root, so this applies to
# every step, not just `sudo` ones.
SAFE_PATH_PREFIXES = ('/var/log/', '/proc/', '/sys/')
SAFE_PATH_EXACT = frozenset({
    '/etc/os-release', '/etc/hostname', '/etc/hosts', '/etc/resolv.conf',
})
# Inside /proc these link to (or dump) the real filesystem or process memory.
PROC_DENY_PARTS = frozenset({'root', 'cwd', 'fd', 'map_files', 'mem', 'environ'})
PROC_DENY_NAMES = frozenset({'kcore', 'kmsg'})
# R2: a glob or a `..` anywhere makes the textual prefix meaningless.
GLOB_CHARS = frozenset('*?[]{}')

# Subcommands for the status verbs that do have them.
_SUBCOMMANDS = {
    'ip': frozenset({'a', 'addr', 'route', 'link'}),
    'systemctl': frozenset({'status', 'is-active', 'is-enabled', 'list-units',
                            'list-timers'}),
    'docker': frozenset({'ps', 'stats', 'images'}),
    'pct': frozenset({'list'}),
    'qm': frozenset({'list'}),
    'zpool': frozenset({'status', 'list'}),
    'zfs': frozenset({'list'}),
}

# Flags whose following token is a value, not an operand.
_VALUE_FLAGS = frozenset({
    '-n', '--lines', '-c', '--bytes', '-m', '--max-count', '-A', '-B', '-C',
    '--context', '--include', '--exclude', '--exclude-from', '-e', '--regexp',
    '-o', '--output', '-t', '--type', '-u', '--unit', '--since', '--until',
    '-p', '--pid', '--user', '-F', '--field', '--format', '-i', '--id',
})
# `--opt=value` names whose value is not a path.
_VALUE_OPTS = frozenset({
    'lines', 'bytes', 'max-count', 'context', 'color', 'colour', 'format',
    'since', 'until', 'type', 'unit', 'pid', 'user', 'id', 'field', 'no-pager',
    'no-stream', 'all', 'human-readable', 'sort', 'time',
})
_JOURNAL_DENY = ('-f', '--follow', '--file', '-D', '--directory', '--root')
_TAIL_DENY = ('-f', '--follow', '-F')
# S1: one auto-run read may not dump a whole journal.
JOURNAL_MAX_LINES = 1000


def is_read_integration_call(tool, transport, args):
    """(ok, reason) — a thin wrapper over Chunk H, no new logic."""
    refusal = _read_only_refusal(tool, transport, args)
    if refusal:
        return False, refusal[0]
    return True, ''


def _path_ok(token: str) -> bool:
    """R2: an absolute path inside the safe trees, with no glob or `..`."""
    if not token.startswith('/'):
        return False
    if any(ch in GLOB_CHARS for ch in token):
        return False
    parts = token.split('/')
    if '..' in parts:
        return False
    if token.startswith('/dev'):
        return False
    if token == '/proc':
        return True
    if token.startswith('/proc'):
        rest = [part for part in parts[2:] if part]
        if not rest:
            return False
        if any(part in PROC_DENY_PARTS for part in rest):
            return False
        if rest[-1] in PROC_DENY_NAMES:
            return False
        return True
    if token in ('/sys', '/var/log'):
        return True
    if token.startswith('/sys/') or token.startswith('/var/log/'):
        return True
    return token in SAFE_PATH_EXACT


def _flag_value(token: str):
    """The value of `--opt=value`, or None."""
    if token.startswith('--') and '=' in token:
        name, _sep, value = token.partition('=')
        return name[2:], value
    return None


def _check_operands(tokens, allow_pattern=False):
    """Every operand must be a safe path; a flag value is skipped."""
    operands = 0
    skip_next = False
    for index, token in enumerate(tokens):
        if skip_next:
            skip_next = False
            continue
        if token.startswith('-'):
            if token in _VALUE_FLAGS:
                skip_next = True
                continue
            pair = _flag_value(token)
            if pair:
                name, value = pair
                if name in _VALUE_OPTS:
                    continue
                if allow_pattern and name in ('regexp', 'pattern'):
                    continue
                # `--file=value` (and friends) is an operand: it must be safe.
                if not _path_ok(value):
                    return False, 'operand:' + value
                operands += 1
                continue
            continue
        if allow_pattern and token.startswith(('^', '.')) and not token.startswith('/'):
            # grep pattern without -e: not a path, but also not a file.
            continue
        if not _path_ok(token):
            return False, 'operand:' + token
        operands += 1
    if operands == 0:
        return False, 'no-operand'
    return True, ''


def _check_status(verb, tokens):
    rest = tokens[1:]
    if verb in _SUBCOMMANDS:
        sub = next((t for t in rest if not t.startswith('-')), '')
        if sub not in _SUBCOMMANDS[verb]:
            return False, 'subcommand:' + (sub or '-')
        if verb == 'docker' and sub == 'stats' and '--no-stream' not in rest:
            return False, 'follows'
        if verb == 'systemctl' and sub in ('list-units', 'list-timers'):
            return True, ''
        return True, ''
    if verb == 'journalctl':
        if any(flag in rest for flag in _JOURNAL_DENY):
            return False, 'journal-flag'
        if '--disk-usage' in rest:
            # Usage statistics only; it never dumps the journal.
            return True, ''
        lines = None
        for index, token in enumerate(rest):
            if token in ('-n', '--lines') and index + 1 < len(rest):
                lines = rest[index + 1]
            elif token.startswith('--lines='):
                lines = token.split('=', 1)[1]
        if lines is None:
            return False, 'journal-no-lines'
        try:
            if int(lines) > JOURNAL_MAX_LINES:
                return False, 'journal-lines'
        except (TypeError, ValueError):
            return False, 'journal-lines'
        return True, ''
    if verb == 'date':
        if any(token in ('-s', '--set') or token.startswith('--set=')
               for token in rest):
            return False, 'date-set'
        return True, ''
    if verb == 'hostname':
        if any(not token.startswith('-') for token in rest):
            return False, 'hostname-set'
        return True, ''
    return True, ''


def _check_file_reader(verb, tokens):
    rest = tokens[1:]
    if verb == 'tail':
        if any(token in _TAIL_DENY or token.startswith('--follow') for token in rest):
            return False, 'follows'
    if verb == 'grep':
        ok, why = _check_operands(rest, allow_pattern=True)
    else:
        ok, why = _check_operands(rest)
    if not ok:
        return False, why
    if verb == 'tail':
        for index, token in enumerate(rest):
            if token in ('-n', '--lines') and index + 1 < len(rest):
                try:
                    if int(rest[index + 1]) > JOURNAL_MAX_LINES:
                        return False, 'tail-lines'
                except (TypeError, ValueError):
                    return False, 'tail-lines'
    return True, ''


def _step_ok(step):
    text = (step.get('text') or '').strip()
    tokens = re.split(r'\s+', text) if text else []
    if not tokens:
        return False, 'no-step'
    # A wrapper other than `sudo` hides part of the command (`env A=…`,
    # `ip netns exec`, `timeout`, `runuser`, …) — refuse rather than guess.
    context = step.get('context') or []
    if context and context != ['sudo']:
        return False, 'context:' + ' '.join(context)
    verb = tokens[0]
    if verb in READ_LANE_VERBS['file']:
        return _check_file_reader(verb, tokens)
    if verb in READ_LANE_VERBS['status']:
        return _check_status(verb, tokens)
    return False, 'verb:' + verb


def is_read_ssh(breakdown):
    """(ok, reason) for a command that may run without a prompt."""
    bd = breakdown or {}
    if not bd.get('complete'):
        return False, 'incomplete'
    steps = bd.get('steps') or []
    if not steps:
        return False, 'no-step'
    flags = set(bd.get('flags') or [])
    if flags - READ_HARMLESS_FLAGS:
        return False, 'flag:' + ','.join(sorted(flags - READ_HARMLESS_FLAGS))
    if bd.get('inner_targets'):
        return False, 'inner-target'
    for step in steps:
        if (step.get('kind') or 'unknown') != 'read':
            return False, 'not-read'
        ok, why = _step_ok(step)
        if not ok:
            return False, why
    return True, ''

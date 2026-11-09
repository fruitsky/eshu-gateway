"""One notion of "read" for auto-run decisions (§ 2.6a, F4).

Chunk H owns the integration definition: `_read_only_refusal` for HTTP tools
(GET/HEAD only) and `is_read_command` for Home Assistant WebSocket commands.
F4 owns the SSH one. Everything that proposes to auto-run a read calls **this
module** — never the underlying checks directly.

The SSH rule is a closed allowlist on top of F1's breakdown. The gateway runs
*every* command as root, so "elevated" is meaningless and a plain `cat`
reaches everything. A command is eligible only when F1 parsed it completely,
every step is one of a small reviewed set of read verbs, no flag is set, every
step only reads inside the safe trees, and there is no inner target.

Round 5 implements the security reviewer's conditions SC1–SC7:

* SC1 — options are allowlisted **per verb and per subcommand**. An option is
  matched by its exact spelling; abbreviations are not options, short clusters
  are allowed only when every letter is on that verb's list, and whether an
  option takes a value is decided per verb. `--` and a lone `-` are refused.
* SC2 — every word that is neither a listed option nor that option's value is
  a file operand and must pass the safe-tree check. The only exception is
  `grep`'s pattern (the first non-option word, and only without `-e`).
* SC3 — a step with an input redirection, a here-string or a heredoc is
  ineligible (F1 flags it and records it per step).
* SC4 — the policy checks subcommands (and `ip`'s action) itself, including
  `zpool`, so it does not rely on F1 alone.
* SC5 — output is bounded: line and byte caps apply to every spelling, "from
  line N" counts as uncapped, `head` has the same cap as `tail`, and log
  files under `/var/log` are read only through `head`, `tail` or `grep -m`.
* SC6 — the safe trees are tighter: `/proc/*/exe`, `pagemap`, `kpage*`,
  `/sys/kernel/{debug,tracing}`, `/sys/fs/bpf`, `/var/log/installer/` and the
  cloud-init logs are refused.
* SC7 is enforced in `core/read_lane.py`: only a plain `jit` or
  `auto_approved` verdict may be auto-run.
"""
import os
import posixpath
import re
import shlex

from core.tool_runner import _read_only_refusal

# R3: any F1 flag blocks unless it is on this harmless list — deliberately
# empty, so `repo-config`, `follows` and any future flag fail closed.
READ_HARMLESS_FLAGS = frozenset()

# R1/SC1: the reviewed verbs. The first group takes no file operands for its
# listed subcommands; the second may only read paths inside the safe trees.
READ_LANE_VERBS = {
    'status': frozenset({
        'uptime', 'df', 'free', 'ss', 'ps', 'uname', 'hostname', 'lsblk',
        'lscpu', 'whoami', 'id', 'date', 'nproc', 'ip', 'systemctl',
        'journalctl', 'docker', 'pct', 'qm', 'zpool', 'zfs',
    }),
    'file': frozenset({'cat', 'head', 'tail', 'grep', 'wc', 'ls', 'stat', 'du'}),
}

# R2/SC6: what a file reader may touch. Every step is root, so this applies
# to every step, not just `sudo` ones.
SAFE_PATH_PREFIXES = ('/var/log/', '/proc/', '/sys/')
SAFE_PATH_EXACT = frozenset({
    '/etc/os-release', '/etc/hostname', '/etc/hosts', '/etc/resolv.conf',
})
# Inside /proc these link to (or dump) the real filesystem, process memory or
# another process's identity.
PROC_DENY_PARTS = frozenset({
    'root', 'cwd', 'fd', 'map_files', 'mem', 'environ',
    'exe',       # SC6: a link into the real filesystem
    'pagemap',   # SC6: huge, and dumps page mappings
})
PROC_DENY_NAMES = frozenset({'kcore', 'kmsg'})
# R2: a glob or a `..` anywhere makes the textual prefix meaningless.
GLOB_CHARS = frozenset('*?[]{}')

# SC6: kernel internals that block, dump kernel data, or hold EFI secrets.
SYS_DENY_PREFIXES = (
    '/sys/firmware',
    '/sys/kernel/debug',
    '/sys/kernel/tracing',
    '/sys/fs/bpf',
)
# SC6: install-time password hashes and cloud-init user-data.
LOG_DENY_PREFIXES = ('/var/log/installer',)
LOG_DENY_NAME_PREFIXES = ('cloud-init',)

# SC1/SC4: the subcommands the policy checks itself.
SUBCOMMANDS = {
    'ip': frozenset({'a', 'addr', 'route', 'link'}),
    'systemctl': frozenset({'status', 'is-active', 'is-enabled',
                            'list-units', 'list-timers'}),
    'docker': frozenset({'ps', 'stats', 'images'}),
    'pct': frozenset({'list'}),
    'qm': frozenset({'list'}),
    'zpool': frozenset({'status', 'list'}),
    'zfs': frozenset({'list'}),
}
# SC4: `ip`'s action is checked too, so `ip addr flush` is not a read.
ACTIONS = {
    'ip': frozenset({'show', 'list', 'ls', 's', 'l'}),
}

# SC5: one auto-run read may not dump a whole journal or a whole file.
JOURNAL_MAX_LINES = 1000
BYTES_MAX = 1024 * 1024
MAX_DEPTH = 16
MAX_COUNT = 1000

_INT_RE = re.compile(r'[0-9]{1,9}')
# SC1: an option value that is a name, not a path — no `/`, no glob, no space.
_NAME_RE = re.compile(r'[A-Za-z0-9_.:@,=+-]{1,64}')


def _bounded_int(value, low, high):
    if not _INT_RE.fullmatch(value or ''):
        return False, 'bad-value'
    number = int(value)
    if number < low or number > high:
        return False, 'cap'
    return True, ''


def _cap_lines(value):
    return _bounded_int(value, 0, JOURNAL_MAX_LINES)


def _cap_bytes(value):
    return _bounded_int(value, 0, BYTES_MAX)


def _cap_depth(value):
    return _bounded_int(value, 0, MAX_DEPTH)


def _cap_count(value):
    return _bounded_int(value, 0, MAX_COUNT)


def _name_value(value):
    if not _NAME_RE.fullmatch(value or ''):
        return False, 'bad-value'
    return True, ''


def _pattern_value(value):
    if not value or len(value) > 512 or any(ord(ch) < 0x20 for ch in value):
        return False, 'bad-value'
    return True, ''


VALIDATORS = {
    'lines': _cap_lines,
    'bytes': _cap_bytes,
    'depth': _cap_depth,
    'count': _cap_count,
    'mcount': _cap_count,
    'text': _name_value,
    'pattern': _pattern_value,
}


def _spec(flags=(), letters='', values=None, words=(), digits=False):
    """One verb's (or subcommand's) options. There is no shared option list."""
    return {
        'flags': frozenset(flags),
        'letters': frozenset(letters),
        'values': dict(values or {}),
        'words': frozenset(words),
        'digits': digits,
    }


# SC1: the whole allowlist. A key of `(verb, '')` is a verb without
# subcommands; every other key is one reviewed subcommand.
OPTION_SPECS = {
    # ── status verbs ────────────────────────────────────────────────────
    ('uptime', ''): _spec(flags=('-p', '--pretty', '-s', '--since')),
    ('df', ''): _spec(flags=(
        '-h', '-H', '-T', '-i', '-P', '-a', '-l',
        '--human-readable', '--print-type', '--inodes', '--portability',
        '--all', '--local')),
    ('free', ''): _spec(flags=(
        '-h', '-b', '-k', '-m', '-g', '-w', '-t', '-l',
        '--human', '--bytes', '--kilo', '--mega', '--giga', '--wide',
        '--total', '--lohi')),
    ('ss', ''): _spec(
        flags=('-t', '-u', '-l', '-n', '-p', '-a', '-4', '-6', '-H', '-s',
               '-i', '-m', '-o', '--tcp', '--udp', '--listening', '--numeric',
               '--processes', '--all', '--ipv4', '--ipv6', '--no-header',
               '--summary', '--info', '--memory', '--options'),
        letters='tulnpa46Hsimo'),
    ('ps', ''): _spec(
        flags=('-e', '-f', '-a', '-u', '-x', '-l', '--all', '--full'),
        letters='efauxl', words=('aux', 'ax', 'x', 'a', 'u')),
    ('uname', ''): _spec(
        flags=('-a', '-s', '-n', '-r', '-v', '-m', '-p', '-i', '-o',
               '--all', '--kernel-name', '--nodename', '--kernel-release',
               '--kernel-version', '--machine', '--processor',
               '--hardware-platform', '--operating-system'),
        letters='asnrvmpio'),
    ('hostname', ''): _spec(),
    ('lsblk', ''): _spec(
        flags=('-a', '-f', '-l', '-n', '-p', '-r', '-t', '-d', '-b', '-J',
               '-P', '--all', '--fs', '--list', '--noheadings', '--paths',
               '--raw', '--topology', '--nodeps', '--bytes', '--json',
               '--pairs'),
        letters='aflnprtdbJ'),
    ('lscpu', ''): _spec(flags=('-J', '--json', '-p', '--parse'), letters='Jp'),
    ('whoami', ''): _spec(),
    ('id', ''): _spec(
        flags=('-u', '-g', '-G', '-n', '-r', '--user', '--group', '--groups',
               '--name', '--real'),
        letters='ugGnr'),
    ('date', ''): _spec(flags=('-u', '--utc', '-R', '--rfc-2822', '--rfc-email'),
                        letters='uR'),
    ('nproc', ''): _spec(flags=('--all',)),
    # `ip`: the flags are exact because several of them are multi-letter
    # (`-br`, `-bri`). `-n`/`-netns` is refused: it runs against another
    # namespace, which SC1 treats like another machine.
    ('ip', 'a'): _spec(flags=(
        '-4', '-6', '-s', '-d', '-br', '-o', '-r', '-j', '-p',
        '--stats', '--details', '--brief', '--oneline', '--resolve',
        '--json', '--pretty')),
    ('ip', 'addr'): _spec(flags=(
        '-4', '-6', '-s', '-d', '-br', '-o', '-r', '-j', '-p',
        '--stats', '--details', '--brief', '--oneline', '--resolve',
        '--json', '--pretty')),
    ('ip', 'route'): _spec(flags=(
        '-4', '-6', '-s', '-d', '-br', '-o', '-r', '-j', '-p',
        '--stats', '--details', '--brief', '--oneline', '--resolve',
        '--json', '--pretty')),
    ('ip', 'link'): _spec(flags=(
        '-4', '-6', '-s', '-d', '-br', '-o', '-r', '-j', '-p',
        '--stats', '--details', '--brief', '--oneline', '--resolve',
        '--json', '--pretty')),
    ('systemctl', 'status'): _spec(
        flags=('-l', '--full', '-a', '--all', '--no-pager', '--no-legend',
               '--plain'),
        letters='la', values={'-n': 'lines', '--lines': 'lines'}),
    ('systemctl', 'is-active'): _spec(flags=('-q', '--quiet', '--no-pager')),
    ('systemctl', 'is-enabled'): _spec(flags=('-q', '--quiet', '--no-pager')),
    ('systemctl', 'list-units'): _spec(
        flags=('-a', '--all', '--no-pager', '--no-legend', '--plain',
               '-l', '--full'),
        letters='la',
        values={'-t': 'text', '--type': 'text', '--state': 'text',
                '-n': 'lines', '--lines': 'lines'}),
    ('systemctl', 'list-timers'): _spec(
        flags=('-a', '--all', '--no-pager', '--no-legend', '--plain',
               '-l', '--full'),
        letters='la',
        values={'-t': 'text', '--type': 'text', '--state': 'text',
                '-n': 'lines', '--lines': 'lines'}),
    ('journalctl', ''): _spec(
        flags=('-b', '--boot', '-k', '--dmesg', '--no-pager', '--no-hostname',
               '--utc', '-r', '--reverse', '-x', '--catalog', '-q', '--quiet',
               '--no-full', '-a', '--all', '-e', '--pager-end',
               '--disk-usage'),
        values={'-n': 'lines', '--lines': 'lines', '-u': 'text',
                '--unit': 'text', '--since': 'text', '--until': 'text',
                '-p': 'text', '--priority': 'text'}),
    ('docker', 'ps'): _spec(
        flags=('-a', '--all', '-q', '--quiet', '-s', '--size', '--no-trunc'),
        letters='aqs', values={'-n': 'count', '--last': 'count'}),
    ('docker', 'stats'): _spec(
        flags=('--no-stream', '-a', '--all', '--no-trunc'), letters='a'),
    ('docker', 'images'): _spec(
        flags=('-a', '--all', '-q', '--quiet', '--no-trunc', '--digests'),
        letters='aq'),
    ('pct', 'list'): _spec(),
    ('qm', 'list'): _spec(),
    ('zpool', 'status'): _spec(
        flags=('-v', '-x', '-H', '-p', '-L', '-P', '-g'), letters='vxHpLPg'),
    ('zpool', 'list'): _spec(
        flags=('-v', '-x', '-H', '-p', '-L', '-P', '-g'), letters='vxHpLPg'),
    ('zfs', 'list'): _spec(
        flags=('-H', '-p', '-r'), letters='Hpr',
        values={'-d': 'depth', '-t': 'text'}),
    # ── file readers ────────────────────────────────────────────────────
    ('cat', ''): _spec(
        flags=('-n', '-b', '-s', '-E', '-T', '-A', '-v', '-e', '-t', '-u'),
        letters='nbsETAvetu'),
    ('head', ''): _spec(
        flags=('-q', '--quiet', '--silent', '-v', '--verbose'),
        letters='qv', digits=True,
        values={'-n': 'lines', '--lines': 'lines',
                '-c': 'bytes', '--bytes': 'bytes'}),
    ('tail', ''): _spec(
        flags=('-q', '--quiet', '--silent', '-v', '--verbose'),
        letters='qv', digits=True,
        values={'-n': 'lines', '--lines': 'lines',
                '-c': 'bytes', '--bytes': 'bytes'}),
    ('grep', ''): _spec(
        flags=('-i', '--ignore-case', '-v', '--invert-match', '-n',
               '--line-number', '-c', '--count', '-l', '--files-with-matches',
               '-L', '--files-without-match', '-o', '--only-matching',
               '-q', '--quiet', '--silent', '-s', '--no-messages',
               '-H', '--with-filename', '-h', '--no-filename',
               '-E', '--extended-regexp', '-F', '--fixed-strings',
               '-G', '--basic-regexp', '-w',
               '--word-regexp', '-x', '--line-regexp', '-a', '--text'),
        letters='ivnclLoqsHhEFGwxa',
        values={'-e': 'pattern', '--regexp': 'pattern',
                '-m': 'mcount', '--max-count': 'mcount',
                '-A': 'lines', '-B': 'lines', '-C': 'lines',
                '--after-context': 'lines', '--before-context': 'lines',
                '--context': 'lines'}),
    ('wc', ''): _spec(flags=('-l', '-w', '-c', '-m', '-L'), letters='lwcmL'),
    ('ls', ''): _spec(
        flags=('-l', '-a', '-A', '-h', '-d', '-t', '-r', '-S', '-i', '-n',
               '-1', '-F', '-R', '-p', '-g', '-o', '--long', '--all',
               '--almost-all', '--human-readable', '--directory',
               '--recursive'),
        letters='laAhdtrSin1FpgRo'),
    ('stat', ''): _spec(flags=('-f', '--file-system', '-t', '--terse'),
                        letters='ft'),
    ('du', ''): _spec(
        flags=('-s', '-h', '-a', '-c', '-x', '-k', '-m', '-b', '-0',
               '--apparent-size', '--total', '--summarize',
               '--human-readable', '--all', '--one-file-system'),
        letters='shacxkmb0', values={'-d': 'depth', '--max-depth': 'depth'}),
}


def option_spec_for(verb, sub):
    """SC1: the one option list for this verb/subcommand, or None."""
    return OPTION_SPECS.get((verb, sub or ''))


def _scan_options(tokens, spec):
    """Consume the listed options. Returns (operands, used, values, error).

    SC1: exact spellings only, `--`/`-` refused, clusters only when every
    letter is listed, and a value is read only for an option this verb says
    takes one. SC2: everything left is an operand.
    """
    operands = []
    used = set()
    values = {}
    index = 0
    total = len(tokens)
    while index < total:
        token = tokens[index]
        if token in ('--', '-'):
            return None, None, None, 'dash'
        if token in spec['words']:
            used.add(token)
            index += 1
            continue
        if token.startswith('--'):
            name, sep, value = token.partition('=')
            if name in spec['values']:
                if not sep:
                    index += 1
                    if index >= total:
                        return None, None, None, 'missing-value'
                    value = tokens[index]
                ok, why = VALIDATORS[spec['values'][name]](value)
                if not ok:
                    return None, None, None, why
                used.add(name)
                values[name] = value
                index += 1
                continue
            if sep or name not in spec['flags']:
                return None, None, None, 'option:' + token
            used.add(name)
            index += 1
            continue
        if token.startswith('-') and len(token) > 1:
            if token in spec['values']:
                index += 1
                if index >= total:
                    return None, None, None, 'missing-value'
                ok, why = VALIDATORS[spec['values'][token]](tokens[index])
                if not ok:
                    return None, None, None, why
                used.add(token)
                values[token] = tokens[index]
                index += 1
                continue
            if token in spec['flags']:
                used.add(token)
                index += 1
                continue
            if spec['digits'] and token[1:].isdigit():
                ok, why = _cap_lines(token[1:])
                if not ok:
                    return None, None, None, why
                used.add('-n')
                values['-n'] = token[1:]
                index += 1
                continue
            cluster = token[1:]
            position = 0
            while position < len(cluster):
                letter = cluster[position]
                option = '-' + letter
                if option in spec['values']:
                    rest = cluster[position + 1:]
                    if rest:
                        value = rest
                    else:
                        index += 1
                        if index >= total:
                            return None, None, None, 'missing-value'
                        value = tokens[index]
                    ok, why = VALIDATORS[spec['values'][option]](value)
                    if not ok:
                        return None, None, None, why
                    used.add(option)
                    values[option] = value
                    break
                if letter not in spec['letters']:
                    return None, None, None, 'option:' + option
                used.add(option)
                position += 1
            index += 1
            continue
        operands.append(token)
        index += 1
    return operands, used, values, ''


def _under(path, prefixes):
    for prefix in prefixes:
        if path == prefix or path.startswith(prefix.rstrip('/') + '/'):
            return True
    return False


def _under_log(path):
    return _under(path, ('/var/log',))


def _cat_allowed(path):
    """SC5: `cat` only for /proc, /sys and the four exact /etc files."""
    if _under(path, ('/proc', '/sys')):
        return True
    return path in SAFE_PATH_EXACT


def _path_ok(token):
    """R2/SC6: an absolute path inside the safe trees, no glob, no `..`.

    Round 6 R1: the path is compared with its normalised form (one trailing
    slash allowed), so an empty component (`/sys/kernel//debug`) or a `.`
    component (`/sys/./kernel/debug`) cannot slip past a textual prefix
    denial. The `..` refusal stays explicit for the reason message.
    """
    if not token.startswith('/'):
        return False
    if any(ch in GLOB_CHARS for ch in token):
        return False
    parts = token.split('/')
    if '..' in parts:
        return False
    stripped = token[:-1] if token.endswith('/') and len(token) > 1 else token
    if posixpath.normpath(stripped) != stripped:
        return False
    if token == '/dev' or token.startswith('/dev/'):
        return False
    if token.startswith('/proc/'):
        rest = [part for part in parts[2:] if part]
        if not rest:
            return False
        if any(part in PROC_DENY_PARTS for part in rest):
            return False
        if rest[-1] in PROC_DENY_NAMES:
            return False
        if any(part.startswith('kpage') for part in rest):
            return False
        return True
    if token.startswith('/sys/'):
        return not _under(token, SYS_DENY_PREFIXES)
    if token.startswith('/var/log/'):
        if _under(token, LOG_DENY_PREFIXES):
            return False
        name = os.path.basename(token)
        if any(name.startswith(prefix) for prefix in LOG_DENY_NAME_PREFIXES):
            return False
        return True
    if token in ('/proc', '/sys', '/var/log'):
        return True
    return token in SAFE_PATH_EXACT


def _grep_log_lines(used, values):
    """R3: how many lines one capped log grep may print.

    `m × (1 + max(A + B, 2C))`, or None when a value cannot be read. The
    reviewer's rule: a context option must not undo the `-m` cap.
    """
    def _int(*names):
        for name in names:
            if name in values:
                try:
                    return int(values[name])
                except (TypeError, ValueError):
                    return None
        return 0

    matches = _int('-m', '--max-count')
    if matches is None:
        return None
    after = _int('-A', '--after-context')
    before = _int('-B', '--before-context')
    context = _int('-C', '--context')
    if after is None or before is None or context is None:
        return None
    return matches * (1 + max(after + before, context * 2))


def _check_files(verb, rest):
    """A file reader: SC2 operands, then the per-verb reading rules."""
    spec = OPTION_SPECS[(verb, '')]
    operands, used, values, error = _scan_options(rest, spec)
    if error:
        return False, error
    if verb == 'grep':
        if '-e' in used or '--regexp' in used:
            files = operands
        elif operands:
            files = operands[1:]       # SC2: the one pattern exception
        else:
            files = []
    else:
        files = operands
    if not files:
        return False, 'no-operand'
    for name in files:
        if not _path_ok(name):
            return False, 'operand:' + name
    if verb == 'grep':
        # R2: recursion is not in the lane — `-r`/`--recursive` are not in the
        # grep spec at all, so they are refused as unknown options before this
        # point. `ls -R` and `du` still list the names.
        if any(_under_log(name) for name in files):
            # SC5: a log is read through grep only with a line cap.
            if '-m' not in used and '--max-count' not in used:
                return False, 'grep-cap'
            # R3: context options multiply the cap, so the product must fit.
            lines = _grep_log_lines(used, values)
            if lines is None or lines > JOURNAL_MAX_LINES:
                return False, 'grep-context'
    if verb == 'ls' and ('-R' in used or '--recursive' in used):
        for name in files:
            if not _under_log(name):
                return False, 'ls-recursive'
    if verb == 'du':
        for name in files:
            if _under(name, ('/proc', '/sys')):
                return False, 'du-recursive'
    if verb == 'cat':
        for name in files:
            if not _cat_allowed(name):
                return False, 'cat-tree:' + name
    if verb == 'wc':
        for name in files:
            if _under_log(name):
                return False, 'wc-log'
    return True, ''


def _check_status(verb, rest):
    """SC1/SC2/SC4: a status verb, its subcommand, its options and operands."""
    sub = ''
    if verb in SUBCOMMANDS:
        index = next((i for i, token in enumerate(rest)
                      if not token.startswith('-')), None)
        if index is None:
            return False, 'subcommand:-'
        sub = rest[index]
        if sub not in SUBCOMMANDS[verb]:
            return False, 'subcommand:' + sub
        rest = rest[:index] + rest[index + 1:]
        if verb in ACTIONS:
            action = next((i for i, token in enumerate(rest)
                           if not token.startswith('-')), None)
            if action is not None:
                if rest[action] not in ACTIONS[verb]:
                    return False, 'action:' + rest[action]
                rest = rest[:action] + rest[action + 1:]
    spec = option_spec_for(verb, sub)
    if spec is None:
        return False, 'verb:' + verb
    operands, used, values, error = _scan_options(rest, spec)
    if error:
        return False, error
    # SC2: a status verb reads no files, so any leftover word must already be
    # a safe path to be allowed through.
    for operand in operands:
        if not _path_ok(operand):
            return False, 'operand:' + operand
    if verb == 'journalctl':
        if '--disk-usage' not in used:
            if '-n' not in used and '--lines' not in used:
                return False, 'journal-no-lines'
    if verb == 'docker' and sub == 'stats' and '--no-stream' not in used:
        return False, 'follows'
    return True, ''


def _step_ok(step):
    """One step of the breakdown: SC1–SC6 for its words."""
    text = (step.get('text') or '').strip()
    try:
        tokens = shlex.split(text)
    except ValueError:
        return False, 'unparsable'
    if not tokens:
        return False, 'no-step'
    # A wrapper other than `sudo` hides part of the command (`env A=…`,
    # `ip netns exec`, `timeout`, `runuser`, …) — refuse rather than guess.
    context = step.get('context') or []
    if context and context != ['sudo']:
        return False, 'context:' + ' '.join(context)
    verb = tokens[0]
    if verb in READ_LANE_VERBS['file']:
        return _check_files(verb, tokens[1:])
    if verb in READ_LANE_VERBS['status']:
        return _check_status(verb, tokens[1:])
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
        # SC3: F1 records input redirections per step (and flags the command).
        if step.get('input_redirects'):
            return False, 'input-redirect'
        if (step.get('kind') or 'unknown') != 'read':
            return False, 'not-read'
        ok, why = _step_ok(step)
        if not ok:
            return False, why
    return True, ''


def is_read_integration_call(tool, transport, args):
    """(ok, reason) — a thin wrapper over Chunk H, no new logic."""
    refusal = _read_only_refusal(tool, transport, args)
    if refusal:
        return False, refusal[0]
    return True, ''

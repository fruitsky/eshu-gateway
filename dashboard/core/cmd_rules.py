"""Verb tables for the F1 command breakdown engine.

`classify(argv)` is the only entry point: it maps a command's argv (already
unwrapped — the parser removes `sudo`, `timeout`, `sh -c` and friends) to
``(kind, why)`` where kind is ``read``, ``write`` or ``unknown``.

The tables are deliberately data-heavy and logic-light so the security-relevant
half of F1 can be reviewed on its own: every verb the approval UI will label
"read" is listed here, and anything not listed is ``unknown``. The argument
limits from chunk rule 8 (`find -exec/-delete`, `awk system(`, `sed -i`,
`xargs`/`watch`/`parallel`) live here too, because they are verb-argument
questions rather than parsing questions.

Context is *not* a licence to guess: when a case is not covered the answer is
``unknown``, never ``read``. Misreporting a write as a read would be a safety
bug in F4; misreporting a read as a write only costs a prompt.
"""

import os
import re

READ = 'read'
WRITE = 'write'
UNKNOWN = 'unknown'

_RANK = {READ: 0, WRITE: 1, UNKNOWN: 2}

# A verb is only trusted to be the command we classified when it is a bare
# name or lives in a system binary directory. `/tmp/x/cat` and `./cat` are
# not `cat` (review round 1, R6).
SYSTEM_BIN_DIRS = frozenset({
    '/bin', '/usr/bin', '/sbin', '/usr/sbin', '/usr/local/bin', '/usr/local/sbin',
})


def resolve_verb(token: str):
    """Return ``(name, allowed)`` for one command word."""
    if not token:
        return '', False
    if '/' not in token:
        return token, True
    return os.path.basename(token), os.path.dirname(token) in SYSTEM_BIN_DIRS


def worst(a: str, b: str) -> str:
    """Return the more alarming of two kinds: unknown > write > read."""
    return a if _RANK[a] >= _RANK[b] else b


# ── Verbs that only ever read ────────────────────────────────────────────────
#
# A verb lands here only when *every* use of it is read-only. Verbs with a
# write path (`date -s`, `hostname NAME`, `sort -o`) are handled below.
READ_VERBS = {
    'cat': 'cat',
    'head': 'head',
    'tail': 'tail',
    'grep': 'grep',
    'egrep': 'grep',
    'fgrep': 'grep',
    'rg': 'ripgrep',
    'ls': 'ls',
    'stat': 'stat',
    'file': 'file',
    'df': 'df',
    'du': 'du',
    'free': 'free',
    'uptime': 'uptime',
    'uname': 'uname',
    'id': 'id',
    'whoami': 'whoami',
    'ps': 'ps',
    'netstat': 'netstat',
    'lsblk': 'lsblk',
    'blkid': 'blkid',
    'findmnt': 'findmnt',
    'sensors': 'sensors',
    'lscpu': 'lscpu',
    'lspci': 'lspci',
    'lsusb': 'lsusb',
    'nproc': 'nproc',
    'wc': 'wc',
    'cut': 'cut',
    'tr': 'tr',
    'echo': 'echo',
    'printf': 'printf',
    'test': 'test',
    '[': 'test',
    'true': 'true',
    'which': 'which',
    'type': 'type',
    'readlink': 'readlink',
    'realpath': 'realpath',
    'basename': 'basename',
    'dirname': 'dirname',
    'jq': 'jq',
    'pwd': 'pwd',
    'printenv': 'printenv',
    'env': 'env',
    'false': 'false',
    'sleep': 'sleep',
    'seq': 'seq',
    'groups': 'groups',
    'getent': 'getent',
    'lsof': 'lsof',
    'pgrep': 'pgrep',
    'pidof': 'pidof',
    'lsns': 'lsns',
    'tty': 'tty',
    'w': 'w',
    'who': 'who',
    'users': 'users',
    'last': 'last',
    'sha256sum': 'sha256sum',
    'sha1sum': 'sha1sum',
    'md5sum': 'md5sum',
    'cksum': 'cksum',
    'diff': 'diff',
    'cmp': 'cmp',
    'column': 'column',
    'rev': 'rev',
    'paste': 'paste',
    'comm': 'comm',
    'lsmod': 'lsmod',
    'modinfo': 'modinfo',
    'locale': 'locale',
    'lsb_release': 'lsb_release',
    'lshw': 'lshw',
    'pveversion': 'pveversion',
    'apt-cache': 'apt-cache',
    'dpkg-query': 'dpkg-query',
    # Shell builtins that only change the shell's own state. They do not
    # touch the host, and the command they prefix is classified on its own.
    # `export`, `unset` and `set` are *not* here: they can change how later
    # commands run (R5) and have handlers below.
    'cd': 'cd (shell state)',
    'pushd': 'pushd (shell state)',
    'popd': 'popd (shell state)',
    'shift': 'shift (shell state)',
    'umask': 'umask (shell state)',
    # Shell control flow: it ends or skips commands, it does not touch the host.
    ':': 'shell control',
    'exit': 'shell control',
    'return': 'shell control',
    'break': 'shell control',
    'continue': 'shell control',
    'wait': 'shell control',
}

# ── Verbs that always write ──────────────────────────────────────────────────
#
# Everything in `cmd_risk.RISK_RULES` that is a verb lands here (or in the
# argument-aware handlers below): systemctl restart/stop, docker rm/rmi,
# journalctl --vacuum, truncate, apt install, pip/npm install, rm, kill -9,
# chmod 777, userdel, find -delete. `--help` is still reported as a write:
# over-reporting a write is the safe direction.
WRITE_VERBS = {
    'rm': 'rm',
    'mv': 'mv',
    'cp': 'cp',
    'mkdir': 'mkdir',
    'rmdir': 'rmdir',
    'touch': 'touch',
    'ln': 'ln',
    'chmod': 'chmod',
    'chown': 'chown',
    'chgrp': 'chgrp',
    'tee': 'tee',
    'dd': 'dd',
    'install': 'install',
    'truncate': 'truncate',
    'kill': 'kill',
    'pkill': 'pkill',
    'killall': 'killall',
    'reboot': 'reboot',
    'shutdown': 'shutdown',
    'poweroff': 'poweroff',
    'halt': 'halt',
    'useradd': 'useradd',
    'usermod': 'usermod',
    'userdel': 'userdel',
    'groupadd': 'groupadd',
    'passwd': 'passwd',
    'ufw': 'ufw',
    'nft': 'nft',
    'umount': 'umount',
    'swapon': 'swapon',
    'swapoff': 'swapoff',
}

# ── Dangerous environment (review round 1, R5) ──────────────────────────────
#
# These names change how a *later* command runs: PATH decides which binary is
# found, LD_* / PYTHON* / PERL5* / RUBY* / NODE_OPTIONS / GIT_* inject code
# into the next process, and the rest change how the shell parses commands.
DANGEROUS_ENV_NAMES = frozenset({
    'PATH', 'BASH_ENV', 'ENV', 'IFS', 'SHELLOPTS', 'BASHOPTS', 'PS4',
    'PROMPT_COMMAND', 'NODE_OPTIONS',
})
DANGEROUS_ENV_PREFIXES = ('LD_', 'PYTHON', 'PERL5', 'RUBY', 'GIT_', 'BASH_FUNC_')

_ASSIGN_NAME_RE = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)=')


def assignment_name(token: str) -> str:
    """The variable name of a `NAME=value` word, or ''."""
    match = _ASSIGN_NAME_RE.match(token or '')
    return match.group(1) if match else ''


def dangerous_env_name(name: str) -> bool:
    """True when setting `name` can change how later commands run."""
    if not name:
        return False
    upper = name.upper()
    if upper in DANGEROUS_ENV_NAMES:
        return True
    return any(upper.startswith(prefix) for prefix in DANGEROUS_ENV_PREFIXES)


# ── Sensitive paths (flag only — F4 refuses them, F1 only labels them) ───────
SENSITIVE_HINTS = (
    '.env',
    '.pem',
    '.key',
    'id_rsa',
    'id_ed25519',
    'authorized_keys',
    '/etc/shadow',
    '/etc/gshadow',
    '/root/.ssh',
    'secret',
    'token',
    'passw',
    'credential',
    'shadow',
    'environ',
    'eshu',
)


_SENSITIVE_RE = re.compile('|'.join(re.escape(hint) for hint in SENSITIVE_HINTS),
                           re.IGNORECASE)


def sensitive_path(token: str) -> bool:
    """True when a command token looks like it touches a secret."""
    return bool(token) and _SENSITIVE_RE.search(token) is not None


def _subcommand(args):
    """First argument that is not a flag, or '' when there is none."""
    for a in args:
        if a == '--':
            continue
        if a.startswith('-') and a != '-':
            continue
        return a
    return ''


def _strip_flags(args, value_flags=()):
    """Drop leading flags; `value_flags` also consume the next argument."""
    i = 0
    while i < len(args):
        a = args[i]
        if a == '--':
            return args[i + 1:]
        if not a.startswith('-') or a == '-':
            return args[i:]
        base = a.split('=', 1)[0]
        if base in value_flags and '=' not in a:
            i += 2
        else:
            i += 1
    return []


def _read(why):
    return READ, why


def _write(why):
    return WRITE, why


def _unknown(why):
    return UNKNOWN, why


# ── Argument-aware handlers ─────────────────────────────────────────────────

_SYSTEMCTL_READ = {
    'status', 'is-active', 'is-enabled', 'is-failed', 'show', 'cat',
    'help', 'list-units', 'list-unit-files', 'list-timers', 'list-sockets',
    'list-dependencies', 'list-jobs', 'list-machines', 'list-automounts',
    'list-paths', 'get-default',
}
_SYSTEMCTL_WRITE = {
    'start', 'stop', 'restart', 'reload', 'try-reload',
    'reload-or-restart', 'try-restart', 'enable', 'disable', 'mask',
    'unmask', 'daemon-reload', 'daemon-reexec', 'kill', 'set-property',
    'edit', 'link', 'revert', 'preset', 'isolate', 'switch-root',
    'freeze', 'thaw', 'set-environment', 'unset-environment',
}


def _systemctl(args):
    sub = _subcommand(args)
    if not sub:
        # `systemctl`, `systemctl --failed`, `systemctl --version`: a listing.
        return _read('systemctl (list)')
    if sub in _SYSTEMCTL_READ or sub.startswith('list-'):
        return _read(f'systemctl {sub}')
    if sub in _SYSTEMCTL_WRITE:
        return _write(f'systemctl {sub}')
    return _unknown(f'systemctl {sub}')


_JOURNAL_WRITE = {'--rotate', '--flush', '--relinquish-var', '--relinquish'}
_JOURNAL_WRITE_PREFIX = ('--vacuum',)


def _journalctl(args):
    for a in args:
        if a in _JOURNAL_WRITE or a.startswith(_JOURNAL_WRITE_PREFIX):
            return _write(f'journalctl {a.split("=", 1)[0]}')
    return _read('journalctl (no vacuum/rotate)')


_DOCKER_READ = {
    'ps', 'logs', 'inspect', 'images', 'version', 'info', 'top', 'port',
    'diff', 'history', 'search', 'df', 'events', 'wait',
}
_DOCKER_WRITE = {
    'run', 'rm', 'rmi', 'stop', 'start', 'restart', 'kill', 'pull', 'build',
    'push', 'create', 'cp', 'commit', 'tag', 'login', 'logout', 'load',
    'save', 'import', 'export', 'pause', 'unpause', 'attach', 'rename',
    'update', 'prune', 'exec', 'compose', 'container', 'image', 'volume',
    'network', 'system', 'plugin', 'secret', 'config', 'node', 'service',
    'swarm', 'stack', 'context', 'trust', 'builder', 'buildx', 'manifest',
}
_COMPOSE_READ = {'ps', 'logs', 'config', 'version', 'top', 'images', 'ls', 'port'}
_COMPOSE_WRITE = {
    'up', 'down', 'pull', 'restart', 'start', 'stop', 'rm', 'build',
    'create', 'run', 'exec', 'kill', 'pause', 'unpause', 'push', 'scale',
    'cp', 'from', 'bridge', 'alpha', 'watch',
}
_DOCKER_GROUPS = {
    'volume': ({'ls', 'inspect'}, {'rm', 'prune', 'create'}),
    'network': ({'ls', 'inspect'}, {'rm', 'prune', 'create', 'connect', 'disconnect'}),
    'image': ({'ls', 'inspect', 'history'}, {'rm', 'prune', 'build', 'pull', 'push', 'tag', 'import', 'load'}),
    'container': ({'ls', 'ps', 'inspect', 'logs', 'top', 'port', 'diff', 'stats'},
                  {'rm', 'stop', 'start', 'restart', 'kill', 'pause', 'unpause', 'prune', 'run', 'create'}),
    'system': ({'df', 'info', 'events', 'version'}, {'prune'}),
    'buildx': ({'ls', 'du', 'inspect', 'version'}, {'build', 'bake', 'prune', 'rm', 'create', 'use'}),
}
_COMPOSE_GLOBAL_VALUE_FLAGS = {
    '-f', '--file', '-p', '--project-name', '--profile', '--env-file',
    '--project-directory', '--ansi', '--progress', '--parallel',
}


def _docker(args):
    rest = _strip_flags(args, {'-H', '--host', '--context', '--config', '-c',
                               '--log-level', '-l', '--tlscacert', '--tlscert',
                               '--tlskey'})
    if not rest:
        return _unknown('docker (no subcommand)')
    sub = rest[0]
    tail = rest[1:]
    if sub == 'compose':
        return _docker_compose(tail)
    if sub == 'stats':
        # `docker stats` streams forever; only --no-stream is a snapshot.
        if '--no-stream' in tail:
            return _read('docker stats --no-stream')
        return _unknown('docker stats (streaming)')
    if sub in _DOCKER_GROUPS:
        read_set, write_set = _DOCKER_GROUPS[sub]
        innermost = _subcommand(_strip_flags(tail))
        if innermost in read_set:
            return _read(f'docker {sub} {innermost}')
        if innermost in write_set:
            return _write(f'docker {sub} {innermost}')
        return _unknown(f'docker {sub} {innermost}'.strip())
    if sub in _DOCKER_READ:
        return _read(f'docker {sub}')
    if sub in _DOCKER_WRITE:
        return _write(f'docker {sub}')
    return _unknown(f'docker {sub}')


def _docker_compose(args):
    rest = _strip_flags(args, _COMPOSE_GLOBAL_VALUE_FLAGS)
    if not rest:
        return _unknown('docker compose (no subcommand)')
    sub = rest[0]
    tail = rest[1:]
    if sub == 'version':
        return _read('docker compose version')
    if sub in _COMPOSE_READ:
        return _read(f'docker compose {sub}')
    if sub in _COMPOSE_WRITE:
        return _write(f'docker compose {sub}')
    return _unknown(f'docker compose {sub}')


_APT_READ = {'list', 'policy', 'search', 'show', 'showsrc', 'depends',
             'rdepends', 'changelog', 'help'}
_APT_WRITE = {'install', 'reinstall', 'remove', 'purge', 'upgrade',
              'full-upgrade', 'dist-upgrade', 'autoremove', 'autoclean',
              'clean', 'update', 'build-dep', 'source', 'download', 'dselect-upgrade'}


def _apt(args):
    sub = _subcommand(args)
    if sub in _APT_READ:
        return _read(f'apt {sub}')
    if sub in _APT_WRITE:
        return _write(f'apt {sub}')
    return _unknown(f'apt {sub}'.strip())


_PIP_READ = {'list', 'show', 'freeze', 'check', 'help', 'index', 'search'}
_PIP_WRITE = {'install', 'uninstall', 'download', 'wheel'}


def _pip(args):
    sub = _subcommand(args)
    if sub in _PIP_READ:
        return _read(f'pip {sub}')
    if sub in _PIP_WRITE:
        return _write(f'pip {sub}')
    return _unknown(f'pip {sub}'.strip())


_NPM_READ = {'ls', 'list', 'view', 'show', 'info', 'outdated', 'fund', 'root',
             'prefix', 'help', 'search', 'why'}
_NPM_WRITE = {'install', 'i', 'ci', 'uninstall', 'remove', 'rm', 'update',
              'upgrade', 'add', 'link', 'prune', 'dedupe', 'publish', 'init'}


def _npm(args):
    sub = _subcommand(args)
    if sub in _NPM_READ:
        return _read(f'npm {sub}')
    if sub in _NPM_WRITE:
        return _write(f'npm {sub}')
    return _unknown(f'npm {sub}'.strip())


_YARN_READ = {'list', 'info', 'why', 'help'}
_YARN_WRITE = {'add', 'install', 'upgrade', 'remove', 'publish', 'init'}


def _yarn(args):
    sub = _subcommand(args)
    if sub in _YARN_READ:
        return _read(f'yarn {sub}')
    if sub in _YARN_WRITE:
        return _write(f'yarn {sub}')
    return _unknown(f'yarn {sub}'.strip())


GIT_READ_SUBCOMMANDS = ('status', 'log', 'diff', 'show')


def _git_subcommand(args):
    """Return (subcommand, tail, uses -c) for the arguments after `git`."""
    i = 0
    while i < len(args) and args[i].startswith('-'):
        a = args[i]
        if a == '--':
            i += 1
            break
        if a == '-c' or (a.startswith('-c') and len(a) > 2):
            return '', args[i:], True
        if a in ('-C', '--git-dir', '--work-tree', '--namespace'):
            i += 2
            continue
        i += 1
    return (args[i] if i < len(args) else ''), args[i + 1:], False


def git_read_subcommand(argv):
    """The git read verb when argv is one of the reads that can run repo
    config (`status`, `log`, `diff`, `show`), else ''. Used to set the
    `repo-config` flag (review round 2, S6)."""
    sub, _tail, config_override = _git_subcommand(argv[1:])
    if config_override:
        return ''
    return sub if sub in GIT_READ_SUBCOMMANDS else ''


def _git(args):
    sub, tail, config_override = _git_subcommand(args)
    if config_override:
        return _unknown('git -c (config override)')
    if sub in GIT_READ_SUBCOMMANDS:
        for arg in tail:
            if arg in ('--ext-diff', '--textconv'):
                return _unknown(f'git {sub} {arg} (runs a configured program)')
            if arg == '--output' or arg.startswith('--output='):
                return _write('git --output (writes a file)')
        return _read(f'git {sub}')
    if sub == 'branch':
        write_flags = {'-d', '-D', '-m', '-M', '-c', '-C', '--delete',
                       '--move', '--copy', '--set-upstream-to',
                       '--unset-upstream', '--edit-description', '-f',
                       '--force'}
        positional = [a for a in tail if not a.startswith('-')]
        if positional or any(a in write_flags or a.startswith('--set-upstream')
                             for a in tail):
            return _write('git branch (changes refs)')
        return _read('git branch (list)')
    return _unknown(f'git {sub}'.strip())


_IP_OBJECTS = {'addr', 'a', 'address', 'link', 'l', 'route', 'r', 'neigh',
               'n', 'neighbor', 'maddr', 'mroute', 'rule', 'tunnel', 'tun',
               'vrf', 'xfrm', 'netns'}
_IP_SHOW = {'show', 'list', 'sh', 'l', 'ls', 's'}
_IP_CHANGE = {'add', 'del', 'delete', 'flush', 'change', 'replace', 'append',
              'prepend', 'set', 'save', 'restore', 'exec', 'update'}


def _ip(args):
    rest = _strip_flags(args, {'-n', '-netns', '-b', '-batch', '-rc', '-rcvbuf'})
    if not rest:
        return _unknown('ip (no object)')
    obj = rest[0]
    if obj not in _IP_OBJECTS:
        return _unknown(f'ip {obj}')
    tail = [a for a in rest[1:] if not a.startswith('-') and a != '--']
    if not tail or tail[0] in _IP_SHOW:
        return _read(f'ip {obj} show')
    if tail[0] in _IP_CHANGE:
        return _write(f'ip {obj} {tail[0]}')
    return _unknown(f'ip {obj} {tail[0]}')


def _mount(args):
    if not args:
        return _read('mount (list)')
    if all(a in ('-l', '--show-labels') for a in args):
        return _read('mount (list)')
    return _write('mount')


_SYSCTL_WRITE_FLAGS = {'-w', '--write', '-p', '--load', '--system'}


def _sysctl(args):
    for a in args:
        if a in _SYSCTL_WRITE_FLAGS or a.startswith('--write') or '=' in a:
            return _write('sysctl (write)')
    return _read('sysctl')


def _date(args):
    for a in args:
        if a in ('-s', '--set') or a.startswith('--set=') or a.startswith('-s'):
            return _write('date -s (sets the clock)')
    return _read('date')


def _hostname(args):
    if [a for a in args if not a.startswith('-')]:
        return _write('hostname (sets the name)')
    return _read('hostname')


def _sort(args):
    for a in args:
        if a.startswith('--compress-program'):
            return _unknown('sort --compress-program (executes)')
        if (a in ('-o', '--output') or a.startswith('--output=')
                or (a.startswith('-o') and not a.startswith('--') and len(a) > 2)):
            return _write('sort -o (writes a file)')
    return _read('sort')


def _uniq(args):
    if len([a for a in args if not a.startswith('-')]) >= 2:
        return _write('uniq (writes a file)')
    return _read('uniq')


def _ss(args):
    if '-K' in args or '--kill' in args:
        return _write('ss -K (kills sockets)')
    return _read('ss')


def _crontab(args):
    if any(a in ('-e', '-r', '-i', '-') for a in args):
        return _write('crontab (edit)')
    if '-l' in args:
        return _read('crontab -l')
    return _write('crontab')


def _find(args):
    write_flags = {'-delete', '-exec', '-execdir', '-ok', '-okdir',
                   '-fprint', '-fprint0', '-fprintf', '-fls'}
    for a in args:
        if a in write_flags:
            return _write(f'find {a}')
    return _read('find')


def _sed_script(args):
    """The sed script(s): every -e expression, or the first positional."""
    scripts = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '-e' or arg == '--expression':
            if i + 1 < len(args):
                scripts.append(args[i + 1])
            i += 2
            continue
        if arg.startswith('--expression='):
            scripts.append(arg.split('=', 1)[1])
            i += 1
            continue
        if arg.startswith('-e') and not arg.startswith('--') and len(arg) > 2:
            scripts.append(arg[2:])
            i += 1
            continue
        i += 1
    if scripts:
        return '\n'.join(scripts)
    for arg in args:
        if not arg.startswith('-') or arg == '-':
            return arg
    return ''


def _sed_inplace(args):
    """True for `-i` anywhere in a short-flag cluster (`-Ei`, `-ni`, `-i.bak`)."""
    for arg in args:
        if arg == '--in-place' or arg.startswith('--in-place='):
            return True
        if arg.startswith('-') and not arg.startswith('--'):
            for ch in arg[1:]:
                if ch == 'i':
                    return True
                if ch in 'efl':  # these take a value; the rest is an argument
                    break
    return False


def _sed_has_script_file(args):
    for arg in args:
        if arg == '--file' or arg.startswith('--file='):
            return True
        if arg.startswith('-') and not arg.startswith('--'):
            for ch in arg[1:]:
                if ch == 'f':
                    return True
                if ch in 'el':
                    break
    return False


def _sed_script_risk(script):
    """Side effects of a sed script itself: `w`/`W` writes, `e` executes."""
    i = 0
    n = len(script)
    while i < n:
        ch = script[i]
        if ch in ' \t\n;{}':
            i += 1
            continue
        if ch in 'wW':
            return _write(f'sed {ch} (writes a file)')
        if ch == 'e':
            return _unknown('sed e (executes a command)')
        if ch == 's':
            delim = script[i + 1] if i + 1 < n else ''
            if not delim or delim.isalnum() or delim in ' \t\n':
                i += 1
                continue
            j = i + 2
            seen = 0
            while j < n:
                if script[j] == '\\':
                    j += 2
                    continue
                if script[j] == delim:
                    seen += 1
                    if seen == 2:
                        break
                j += 1
            if seen < 2:
                return None
            k = j + 1
            while k < n and script[k].isalpha():
                flag = script[k]
                if flag == 'e':
                    return _unknown('sed s///e (executes)')
                if flag in ('w', 'W'):
                    return _write('sed s///w (writes a file)')
                k += 1
            i = k
            continue
        i += 1
    return None


def _sed(args):
    if _sed_inplace(args):
        return _write('sed -i')
    if _sed_has_script_file(args):
        return _unknown('sed -f (script file)')
    script = _sed_script(args)
    if script:
        risk = _sed_script_risk(script)
        if risk:
            return risk
    return _read('sed')


_AWK_STRING_RE = re.compile(r'"(?:\\.|[^"\\])*"')


def _awk_statements(program):
    """Split an awk program on ; { } and newline, outside string literals."""
    statements = []
    current = []
    in_string = False
    i = 0
    n = len(program)
    while i < n:
        ch = program[i]
        if in_string:
            current.append(ch)
            if ch == '\\' and i + 1 < n:
                current.append(program[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            current.append(ch)
            i += 1
            continue
        if ch in ';{}\n':
            statements.append(''.join(current))
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    statements.append(''.join(current))
    return statements


def _awk_output_risk(joined):
    """A print/printf statement that redirects to a file or pipes to a command."""
    for statement in _awk_statements(joined):
        stripped = statement.strip()
        if not re.match(r'(?:print|printf)\b', stripped):
            continue
        bare = _AWK_STRING_RE.sub('""', stripped)
        if '|' in bare:
            return _unknown('awk print | (pipes to a command)')
        if '>' in bare:
            return _write('awk print > (writes a file)')
    return None


def _awk(args):
    joined = ' '.join(args)
    if re.search(r'system\s*\(', joined) or re.search(r'\|\s*getline', joined):
        return _unknown('awk system()/getline (executes)')
    # `print … | "cmd"` pipes the record into a command; `@include` loads code.
    if re.search(r'\|\s*[\'"]', joined):
        return _unknown('awk pipe to a command (executes)')
    if '@include' in joined:
        return _unknown('awk @include (loads code)')
    output = _awk_output_risk(joined)
    if output:
        return output
    if re.search(r'\b(?:print|printf)\s*>>?', joined):
        return _write('awk print > (writes a file)')
    for a in args:
        if a == '--file' or a.startswith('--file='):
            return _unknown('awk -f (program file)')
        if a.startswith('-f') and not a.startswith('--'):
            return _unknown('awk -f (program file)')
        if a in ('-i', '--include') or (a.startswith('-i') and not a.startswith('--')):
            return _write('awk -i inplace')
    return _read('awk')


def _yq(args):
    for a in args:
        if a in ('-i', '--inplace', '--in-place'):
            return _write('yq -i')
    return _read('yq')


_DPKG_WRITE = {'-i', '--install', '-r', '--remove', '-P', '--purge',
               '--configure', '--unpack', '--set-selections',
               '--clear-selections'}
_DPKG_READ = {'-l', '--list', '-s', '--status', '-L', '--listfiles',
              '--get-selections', '--print-architecture', '--version'}


def _dpkg(args):
    for a in args:
        if a in _DPKG_WRITE:
            return _write(f'dpkg {a}')
    for a in args:
        if a in _DPKG_READ:
            return _read(f'dpkg {a}')
    return _unknown('dpkg')


def _apt_config(args):
    if _subcommand(args) in ('dump', 'list'):
        return _read('apt-config')
    return _unknown('apt-config')


_SMARTCTL_READ = {'-a', '-H', '-i', '-x', '--all', '--health', '--info',
                  '--xall', '--scan'}


_SMARTCTL_WRITE = {'-s', '--smart', '-t', '--test', '-X', '--abort', '-o',
                   '--offlineauto', '-S', '--saveauto', '--set'}
_SMARTCTL_WRITE_PREFIX = ('--set', '--test', '--smart', '--abort',
                          '--offlineauto', '--saveauto')


def _smartctl(args):
    # Action flags win over read flags: `smartctl -a -t long` starts a test.
    for a in args:
        if a in _SMARTCTL_WRITE or a.startswith(_SMARTCTL_WRITE_PREFIX):
            return _write(f'smartctl {a}')
    for a in args:
        if a in _SMARTCTL_READ:
            return _read(f'smartctl {a}')
    return _unknown('smartctl')


_NVME_READ = {'smart-log', 'list', 'list-subsys', 'id-ctrl', 'id-ns',
              'error-log', 'fw-log', 'get-feature', 'show-regs'}


def _tree(args):
    for a in args:
        if (a in ('-o', '--output') or a.startswith('--output=')
                or (a.startswith('-o') and not a.startswith('--') and len(a) > 2)):
            return _write('tree -o (writes a file)')
    return _read('tree')


def _dmidecode(args):
    for a in args:
        if a == '--dump-bin' or a.startswith('--dump-bin='):
            return _write('dmidecode --dump-bin (writes a file)')
    return _read('dmidecode')


def _systemd_analyze(args):
    sub = _subcommand(args)
    if sub.startswith('set-'):
        return _write(f'systemd-analyze {sub}')
    return _read('systemd-analyze')


_NVIDIA_READ_FLAGS = {'-q', '--query', '-L', '--list-gpus', '--format',
                      '-i', '--id', '--query-gpu', '--query-compute-apps'}
_NVIDIA_VALUE_FLAGS = {'-i', '--id', '--format', '--query-gpu',
                       '--query-compute-apps'}


def _nvidia_smi(args):
    if not args:
        return _read('nvidia-smi')
    i = 0
    while i < len(args):
        arg = args[i]
        base = arg.split('=', 1)[0]
        if base in _NVIDIA_READ_FLAGS or base.startswith('--query'):
            if base in _NVIDIA_VALUE_FLAGS and '=' not in arg:
                i += 2
            else:
                i += 1
            continue
        return _unknown(f'nvidia-smi {arg}')
    return _read('nvidia-smi')


def _vcgencmd(args):
    sub = _subcommand(args)
    if sub and (sub.startswith(('measure_', 'get_')) or sub in ('version', 'commands')):
        return _read(f'vcgencmd {sub}')
    return _unknown('vcgencmd')


_TAR_WRITE_MODES = set('xcruA')
_TAR_READ_MODES = set('t')
_TAR_VALUE_LETTERS = set('fCTXbgHKLVN')


# `--to-command` was already reported as a write via its -x sibling (review
# round 2 called it right), so it is deliberately not listed here.
_TAR_EXEC_OPTIONS = {'--use-compress-program', '--info-script',
                     '--new-volume-script', '--rsh-command', '--rmt-command'}


def _tar_executes(args):
    """tar options that run a program (review round 2, S4)."""
    merged = ' '.join(args)
    if re.search(r'--checkpoint-action[= ]\S*exec', merged):
        return True
    for arg in args:
        base = arg.split('=', 1)[0]
        if base in _TAR_EXEC_OPTIONS:
            return True
        if arg.startswith('-I') and not arg.startswith('--'):
            return True
        if arg.startswith('-F') and not arg.startswith('--'):
            return True
        if arg.startswith('--checkpoint-action') and 'exec' in arg:
            return True
    return False


def _tar(args):
    if _tar_executes(args):
        return _unknown('tar runs a program')
    for arg in args:
        if arg.startswith('--'):
            if arg in ('--list',):
                return _read('tar --list')
            if arg in ('--extract', '--get', '--create', '--append', '--update',
                       '--catenate', '--delete', '--diff', '--compare'):
                return _write(f'tar {arg}')
    for arg in args:
        if arg.startswith('--'):
            continue
        letters = arg[1:] if arg.startswith('-') else arg
        if not letters:
            continue
        for ch in letters:
            if ch in _TAR_VALUE_LETTERS:
                break
            if ch in _TAR_WRITE_MODES:
                return _write('tar (writes)')
            if ch in _TAR_READ_MODES:
                return _read('tar --list')
        if not arg.startswith('-'):
            break
    return _unknown('tar')


def _declaration(args):
    """export / declare / typeset / readonly / local — read unless dangerous."""
    for arg in args:
        if arg.startswith('-'):
            continue
        name = assignment_name(arg) or arg
        if dangerous_env_name(name):
            return _unknown('changes how later commands run')
    return _read('shell state')


def _shell_state(args):
    return _unknown('can change how later commands run')


def _set_builtin(args):
    """`set -e`, `set -euo pipefail`, `set +x`, `set -- a b`: shell options and
    positional parameters only — they cannot change how later commands resolve
    (review round 2, S7)."""
    return _read('shell options')


def _read_builtin(args):
    for arg in args:
        if arg.startswith('-'):
            continue
        if dangerous_env_name(arg):
            return _unknown('changes how later commands run')
    return _read('read')


def _printf(args):
    for i, arg in enumerate(args):
        if arg == '-v' and i + 1 < len(args) and dangerous_env_name(args[i + 1]):
            return _unknown('changes how later commands run')
    return _read('printf')


def _nvme(args):
    sub = _subcommand(args)
    if sub in _NVME_READ:
        return _read(f'nvme {sub}')
    return _unknown(f'nvme {sub}'.strip())


def _hostnamectl(args):
    sub = _subcommand(args)
    if not sub or sub == 'status':
        return _read('hostnamectl')
    if sub in ('set-hostname', 'set-chassis', 'set-deployment',
               'set-location', 'set-icon-name'):
        return _write(f'hostnamectl {sub}')
    return _unknown(f'hostnamectl {sub}')


def _timedatectl(args):
    sub = _subcommand(args)
    if not sub or sub in ('status', 'show', 'list-timezones', 'timesync-status'):
        return _read('timedatectl')
    if sub in ('set-time', 'set-timezone', 'set-ntp', 'set-local-rtc'):
        return _write(f'timedatectl {sub}')
    return _unknown(f'timedatectl {sub}')


def _dmesg(args):
    for a in args:
        if a in ('-c', '-C', '--read-clear', '--clear'):
            return _write('dmesg -c (clears the ring buffer)')
    return _read('dmesg')


def _top(args):
    if '-b' in args or '--batch' in args:
        return _read('top -b')
    return _unknown('top (interactive)')


def _pct(args):
    if _subcommand(args) in ('list', 'status', 'config'):
        return _read(f'pct {_subcommand(args)}')
    return _unknown(f'pct {_subcommand(args)}'.strip())


def _qm(args):
    if _subcommand(args) in ('list', 'status', 'config'):
        return _read(f'qm {_subcommand(args)}')
    return _unknown(f'qm {_subcommand(args)}'.strip())


def _zpool(args):
    if _subcommand(args) in ('status', 'list'):
        return _read(f'zpool {_subcommand(args)}')
    return _unknown(f'zpool {_subcommand(args)}'.strip())


def _zfs(args):
    if _subcommand(args) in ('list', 'get'):
        return _read(f'zfs {_subcommand(args)}')
    return _unknown(f'zfs {_subcommand(args)}'.strip())


_IPTABLES_WRITE = {'-A', '-I', '-D', '-F', '-X', '-P', '-N', '-R', '-E', '-Z'}
_IPTABLES_READ = {'-L', '-S', '-C', '--list', '--list-rules', '--check'}


def _iptables(args):
    for a in args:
        if a in _IPTABLES_WRITE:
            return _write(f'iptables {a}')
    for a in args:
        if a in _IPTABLES_READ:
            return _read('iptables -L')
    if not args:
        return _unknown('iptables (no arguments)')
    return _unknown('iptables')


def _python(args):
    if args and all(a in ('-V', '--version', '-VV') for a in args):
        return _read('python --version')
    return _unknown('python (executes code)')


def _node(args):
    if args and all(a in ('-v', '--version') for a in args):
        return _read('node --version')
    return _unknown('node (executes code)')


def _command(args):
    if args and args[0] in ('-v', '-V', '--version'):
        return _read('command -v')
    return _unknown('command (executes)')


_CURL_WRITE_FLAGS = {'-o', '-O', '-T', '-d', '-F', '-X', '--output',
                     '--upload-file', '--request', '--form', '--json',
                     '--remote-name', '--data', '--data-raw', '--data-binary',
                     '--data-urlencode', '--post-data', '--post-file'}
_CURL_READ_METHODS = {'GET', 'HEAD', 'OPTIONS'}


def _http_fetch(args):
    """`curl`/`wget` are unknown to F4, but say when the request only GETs."""
    for i, a in enumerate(args):
        base = a.split('=', 1)[0]
        if base in ('-X', '--request'):
            method = a.split('=', 1)[1] if '=' in a else (
                args[i + 1] if i + 1 < len(args) else 'GET')
            if method.upper() not in _CURL_READ_METHODS:
                return _unknown(f'network request ({method.upper()})')
            continue
        if base in _CURL_WRITE_FLAGS or a.startswith('--data'):
            return _unknown('network request with a body')
    return _unknown('network fetch (GET)')


def _ssh(args):
    return _unknown('ssh (nested session)')


def _opaque(args):
    return _unknown('cannot tell what it runs')


_HANDLERS = {
    'systemctl': _systemctl,
    'journalctl': _journalctl,
    'docker': _docker,
    'docker-compose': _docker_compose,
    'apt': _apt,
    'apt-get': _apt,
    'dnf': _apt,
    'yum': _apt,
    'pip': _pip,
    'pip3': _pip,
    'npm': _npm,
    'yarn': _yarn,
    'git': _git,
    'ip': _ip,
    'mount': _mount,
    'sysctl': _sysctl,
    'date': _date,
    'hostname': _hostname,
    'sort': _sort,
    'tree': _tree,
    'dmidecode': _dmidecode,
    'systemd-analyze': _systemd_analyze,
    'nvidia-smi': _nvidia_smi,
    'vcgencmd': _vcgencmd,
    'tar': _tar,
    'install': lambda args: _write('install'),
    'export': _declaration,
    'declare': _declaration,
    'typeset': _declaration,
    'readonly': _declaration,
    'local': _declaration,
    'set': _set_builtin,
    'unset': _shell_state,
    'alias': _shell_state,
    'hash': _shell_state,
    'enable': _shell_state,
    'trap': _shell_state,
    'source': _shell_state,
    '.': _shell_state,
    'read': _read_builtin,
    'printf': _printf,
    'uniq': _uniq,
    'ss': _ss,
    'crontab': _crontab,
    'find': _find,
    'sed': _sed,
    'awk': _awk,
    'yq': _yq,
    'dpkg': _dpkg,
    'apt-config': _apt_config,
    'smartctl': _smartctl,
    'nvme': _nvme,
    'dmesg': _dmesg,
    'hostnamectl': _hostnamectl,
    'timedatectl': _timedatectl,
    'top': _top,
    'pct': _pct,
    'qm': _qm,
    'zpool': _zpool,
    'zfs': _zfs,
    'iptables': _iptables,
    'python': _python,
    'python3': _python,
    'node': _node,
    'command': _command,
    'curl': _http_fetch,
    'wget': _http_fetch,
    'ssh': _ssh,
    'scp': _ssh,
    'rsync': _ssh,
    'xargs': _opaque,
    'watch': _opaque,
    'parallel': _opaque,
    'eval': _opaque,
}


def classify(argv):
    """Return ``(kind, why)`` for an unwrapped argv."""
    if not argv:
        return UNKNOWN, 'empty command'
    verb, allowed = resolve_verb(argv[0])
    if not allowed:
        return UNKNOWN, 'runs a binary outside the system paths'
    if not verb:
        return UNKNOWN, 'empty command'
    if verb.startswith('mkfs'):
        return WRITE, verb
    args = argv[1:]
    handler = _HANDLERS.get(verb)
    if handler is not None:
        return handler(args)
    if verb in WRITE_VERBS:
        return WRITE, WRITE_VERBS[verb]
    if verb in READ_VERBS:
        return READ, READ_VERBS[verb]
    return UNKNOWN, f'unrecognised command ({verb})'

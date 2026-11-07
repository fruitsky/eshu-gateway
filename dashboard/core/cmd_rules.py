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
    'tree': 'tree',
    'column': 'column',
    'rev': 'rev',
    'paste': 'paste',
    'comm': 'comm',
    'lsmod': 'lsmod',
    'modinfo': 'modinfo',
    'locale': 'locale',
    'lsb_release': 'lsb_release',
    'dmidecode': 'dmidecode',
    'lshw': 'lshw',
    'nvidia-smi': 'nvidia-smi',
    'pveversion': 'pveversion',
    'vcgencmd': 'vcgencmd',
    'systemd-analyze': 'systemd-analyze',
    'apt-cache': 'apt-cache',
    'dpkg-query': 'dpkg-query',
    # Shell builtins that only change the shell's own state. They do not
    # touch the host, and the command they prefix is classified on its own.
    'cd': 'cd (shell state)',
    'pushd': 'pushd (shell state)',
    'popd': 'popd (shell state)',
    'export': 'export (shell state)',
    'unset': 'unset (shell state)',
    'set': 'set (shell state)',
    'shift': 'shift (shell state)',
    'umask': 'umask (shell state)',
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


def _git(args):
    i = 0
    while i < len(args) and args[i].startswith('-'):
        a = args[i]
        if a == '--':
            i += 1
            break
        if a in ('-c',) or (a.startswith('-c') and len(a) > 2):
            return _unknown('git -c (config override)')
        if a in ('-C', '--git-dir', '--work-tree', '--namespace'):
            i += 2
            continue
        i += 1
    sub = args[i] if i < len(args) else ''
    tail = args[i + 1:]
    if sub in ('status', 'log', 'diff', 'show'):
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
        if a in ('-o', '--output') or a.startswith('--output='):
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
                   '-fprint', '-fprintf', '-fls'}
    for a in args:
        if a in write_flags:
            return _write(f'find {a}')
    return _read('find')


def _sed_script(args):
    """The sed script: the argument after -e, or the first positional."""
    for i, a in enumerate(args):
        if a == '-e' or a == '--expression':
            return args[i + 1] if i + 1 < len(args) else ''
        if a.startswith('--expression='):
            return a.split('=', 1)[1]
    for a in args:
        if not a.startswith('-') or a == '-':
            return a
    return ''


def _sed(args):
    for a in args:
        if a == '--in-place' or a.startswith('--in-place='):
            return _write('sed -i')
        if a.startswith('-i') and not a.startswith('--'):
            return _write('sed -i')
    script = _sed_script(args)
    if script:
        if re.search(r'(?:^|[;\n])\s*[wW]\s+\S', script) or re.search(r'/\s*w\s+\S', script):
            return _write('sed w (writes a file)')
        if re.search(r'[/|]\s*e(?:\s|$)', script) or re.search(r'(?:^|[;\n])\s*e(?:\s|$)', script):
            return _unknown('sed e (executes)')
    return _read('sed')


def _awk(args):
    joined = ' '.join(args)
    if 'system(' in joined or '| getline' in joined:
        return _unknown('awk system() (executes)')
    if re.search(r'\b(?:print|printf)\s*>>?', joined):
        return _write('awk print > (writes a file)')
    for a in args:
        if a in ('-i', '--include') or a.startswith('-i'):
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


def _smartctl(args):
    for a in args:
        if a in _SMARTCTL_READ:
            return _read(f'smartctl {a}')
    return _unknown('smartctl')


_NVME_READ = {'smart-log', 'list', 'list-subsys', 'id-ctrl', 'id-ns',
              'error-log', 'fw-log', 'get-feature', 'show-regs'}


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
    verb = os.path.basename(argv[0])
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

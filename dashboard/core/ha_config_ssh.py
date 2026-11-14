"""F11: edit Home Assistant YAML through the Advanced SSH & Web Terminal add-on.

The add-on is not an Eshu gateway (Alpine, no systemd, rebuilt on update), so
the dashboard opens the SSH connection itself. The transport reuses F12's
hardened client (`core/ssh_client`) with a per-integration key and a pinned
host key; every command is one of the fixed templates below, never agent text.
The path rules and the write preflight/scan live in
`core/ha_config_preflight.py` (part 1).

Two invariants shape the write path:

* **one connection, one script** — realpath, hash check, backup, write,
  mode/owner copy, `mv`, `ha core check` and the automatic restore all happen
  in a single remote `sh` under a remote `timeout 100`, with one exit code per
  step. A dead SSH client cannot leave a second writer behind.
* **unknown is not failure** — an exit outside the script's known range (or a
  client timeout) leaves the backup on disk and is reported as
  `config_state_unknown`; the integration then refuses writes until a
  `ha_config_read` of the same path shows which content is in place.
"""
import os
import re
import secrets
import shlex
import tempfile
import threading
import time

from core import ssh_client, ssh_keys
from core.ha_config_preflight import (
    ConfigError, MAX_CONTENT, ROOT, SECRETS, check_write_path, scan,
    to_root_path, unified_diff, validate_write,
)
from core.secret_scrub import scrub_string
from db.audit import record_audit_event
from db.integrations import (
    get_integration_config, record_integration_call, set_integration_config,
)
from db.misc import get_meta_json, set_meta_json

TIMEOUT_SHORT = 30
TIMEOUT_LONG = 120
MAX_LIST = 200
USER_RE = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9_.-]{0,31}$')
HOST_RE = re.compile(r'^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,252})$')
BACKUP_RE = re.compile(r'^' + re.escape(ROOT) + r'/[A-Za-z0-9._/-]{1,220}'
                       r'\.eshu-[0-9]{10}\.bak$')
REFUSED_DIRS = ('.storage/', '.ssh/', 'deps/', 'tts/')
REFUSED_SUFFIXES = ('.db', '.db-wal', '.db-shm')

# W4: /config is a symlink to /homeassistant on the add-on; BusyBox find
# does not follow a symlinked start point, so both walks start at the real
# root. U2: packages/ is walked recursively and is writable.
LIST_SCRIPT = (
    "find /homeassistant -maxdepth 1 -type f "
    "\\( -name '*.yaml' -o -name '*.yml' \\) "
    "-exec stat -c '%s %Y %n' {} \\; ; "
    "find /homeassistant/packages -type f "
    "\\( -name '*.yaml' -o -name '*.yml' \\) "
    "-exec stat -c '%s %Y %n' {} \\; 2>/dev/null"
)

# H2/U1-U4/W1-W4: one script, one connection, one exit code per step.
WRITE_SCRIPT = r'''
set -eu
path=$1; expect=$2; backup=$3; tmp=$4
case "$path"   in /*) ;; *) exit 19 ;; esac
case "$backup" in /*) ;; *) exit 19 ;; esac
case "$tmp"    in /*) ;; *) exit 19 ;; esac
real=$(realpath "$path") || exit 12
[ -f "$real" ] || exit 12
dir=$(dirname "$real")
case "$dir" in
  /homeassistant|/homeassistant/packages|/homeassistant/packages/*) ;;
  *) exit 11 ;;
esac
[ "$(realpath "$(dirname "$path")")" = "$dir" ] || exit 13
[ "$(basename "$real")" = "$(basename "$path")" ] || exit 13
case "$backup" in
  "$real".eshu-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].bak) ;;
  *) exit 19 ;;
esac
case "$tmp" in
  "$real".eshu-tmp-*) ;;
  *) exit 19 ;;
esac
[ "$(dirname "$backup")" = "$dir" ] || exit 19
[ "$(dirname "$tmp")" = "$dir" ] || exit 19
[ ! -e "$backup" ] || exit 19
[ ! -e "$tmp" ] || exit 19
[ "$(sha256sum "$real" | cut -d" " -f1)" = "$expect" ] || exit 14
cp -p "$real" "$backup" || exit 15
cat > "$tmp" || { rm -f "$tmp"; exit 16; }
chmod "$(stat -c %a "$real")" "$tmp" || exit 16
chown "$(stat -c %u:%g "$real")" "$tmp" 2>/dev/null || true
mv -f "$tmp" "$real" || exit 17
timeout 100 ha core check 2>&1 || { cp -p "$backup" "$real" || exit 1; exit 18; }
printf "ok %s\n" "$(sha256sum "$real" | cut -d" " -f1)"
'''

RESTORE_SCRIPT = r'''
set -eu
path=$1; backup=$2; tmp=$3
case "$path"   in /*) ;; *) exit 19 ;; esac
case "$backup" in /*) ;; *) exit 19 ;; esac
case "$tmp"    in /*) ;; *) exit 19 ;; esac
real=$(realpath "$path") || exit 12
[ -f "$real" ] || exit 12
dir=$(dirname "$real")
case "$dir" in
  /homeassistant|/homeassistant/packages|/homeassistant/packages/*) ;;
  *) exit 11 ;;
esac
[ "$(realpath "$(dirname "$path")")" = "$dir" ] || exit 13
[ "$(basename "$real")" = "$(basename "$path")" ] || exit 13
case "$backup" in
  "$real".eshu-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].bak) ;;
  *) exit 19 ;;
esac
case "$tmp" in
  "$real".eshu-tmp-*) ;;
  *) exit 19 ;;
esac
[ "$(dirname "$backup")" = "$dir" ] || exit 19
[ "$(dirname "$tmp")" = "$dir" ] || exit 19
[ -f "$backup" ] || exit 12
[ ! -e "$tmp" ] || exit 19
cp -p "$real" "$tmp" || exit 20
cp -p "$backup" "$real" || { rm -f "$tmp"; exit 21; }
timeout 100 ha core check 2>&1 || { cp -p "$tmp" "$real" || exit 1; rm -f "$tmp"; exit 18; }
rm -f "$tmp"
printf "ok %s\n" "$(sha256sum "$real" | cut -d" " -f1)"
'''


class HaConfigError(ValueError):
    """A refused or failed HA config call with a stable code."""

    def __init__(self, code, message=''):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def _now():
    return int(time.time())


# ── the integration record ─────────────────────────────────────────────

def target(integration):
    """`{'ip', 'port', 'user'}` for the add-on, from base_url + config_json."""
    cfg = get_integration_config(integration)
    base = str((integration or {}).get('base_url') or '').strip()
    host, _, port_text = base.rpartition(':')
    if host and port_text.isdigit():
        port = int(port_text)
    else:
        host, port = base, 22
    host = host.strip('[]')
    if not HOST_RE.match(host or ''):
        raise HaConfigError('config_integration_invalid',
                            'the integration base_url must be host or host:port')
    try:
        port = int(cfg.get('ssh_port') or port or 22)
    except (TypeError, ValueError):
        raise HaConfigError('config_integration_invalid', 'invalid ssh port')
    if not 1 <= port <= 65535:
        raise HaConfigError('config_integration_invalid', 'invalid ssh port')
    user = str(cfg.get('ssh_user') or 'root')
    if not USER_RE.match(user):
        raise HaConfigError('config_integration_invalid', 'invalid ssh user')
    return {'ip': host, 'port': port, 'user': user}


def _key_material(integration):
    key = str((integration or {}).get('secret') or '')
    if 'PRIVATE KEY' not in key:
        raise HaConfigError('config_key_missing',
                            'the integration has no SSH private key')
    return key if key.endswith('\n') else key + '\n'


def _known_hosts_host(target_):
    if target_.get('port') and int(target_['port']) != 22:
        return '[%s]:%s' % (target_['ip'], target_['port'])
    return target_['ip']


def _pin_state(integration):
    cfg = get_integration_config(integration)
    return str(cfg.get('host_key') or ''), str(cfg.get('host_fingerprint') or '')


def _save_pin(integration, key_part):
    fingerprint = ssh_keys.fingerprint(key_part)
    set_integration_config(integration, {'host_key': key_part,
                                         'host_fingerprint': fingerprint})
    return fingerprint


# ── freeze, lock and the unknown-state record ──────────────────────────

def _frozen():
    from db.gateways import get_trigger_freeze
    return get_trigger_freeze() is not None


_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def _integration_lock(integration):
    name = (integration or {}).get('name') or ''
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(name, threading.Lock())
    if not lock.acquire(blocking=False):
        raise HaConfigError('config_busy',
                            'another write or restore is in flight for this '
                            'integration')
    return lock


def _state_key(integration):
    return 'ha_config_state_' + ((integration or {}).get('name') or '')


def pending_state(integration):
    return get_meta_json(_state_key(integration), {}) or {}


def _set_pending(integration, state):
    set_meta_json(_state_key(integration), state)


def _clear_pending(integration):
    _set_pending(integration, {})


def _check_key(integration):
    return 'ha_check_config_' + ((integration or {}).get('name') or '')


def _invalidate_check(integration):
    """H5: a write makes every earlier config check stale before it starts."""
    set_meta_json(_check_key(integration), {})


def _record_check(integration, valid):
    if valid:
        set_meta_json(_check_key(integration),
                      {'result': 'valid', 'at': _now(), 'source': 'ha_ssh'})


# ── path policy ────────────────────────────────────────────────────────

def _rel_parts(root_path):
    rel = root_path[len(ROOT) + 1:]
    return rel, rel.split('/')


def check_read_path(path):
    """A path the read/list tools may touch, as the add-on sees it."""
    text = to_root_path(path)
    if not text.startswith(ROOT + '/'):
        raise HaConfigError('config_path_refused',
                            'the path must be under /config')
    rel, parts = _rel_parts(text)
    if not rel or any(p in ('', '.', '..') or p.startswith('.') for p in parts):
        raise HaConfigError('config_path_refused',
                            'the path has an empty or dot segment')
    if not rel.endswith(('.yaml', '.yml')):
        raise HaConfigError('config_path_refused', 'only .yaml/.yml are read')
    if '/' in rel and not rel.startswith('packages/'):
        raise HaConfigError('config_path_refused',
                            'only top-level files or packages/ may be read')
    low = rel.lower()
    if any(low.startswith(d) for d in REFUSED_DIRS):
        raise HaConfigError('config_path_refused',
                            'that directory is not config')
    if low.endswith(REFUSED_SUFFIXES):
        raise HaConfigError('config_path_refused', 'that file type is not config')
    return text


def _display_path(root_path):
    """`/homeassistant/x` -> `/config/x` (the form agents and operators use)."""
    if root_path.startswith(ROOT + '/'):
        return '/config' + root_path[len(ROOT):]
    return root_path


def _mask_secrets(content):
    """Keys only, values replaced. Comments and structure are kept."""
    out = []
    for line in str(content or '').splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or ':' not in line:
            out.append(line)
            continue
        key, _sep, _value = line.partition(':')
        out.append(key + ': "***"')
    return '\n'.join(out) + ('\n' if str(content or '').endswith('\n') else '')


# ── the transport ──────────────────────────────────────────────────────

def _record(integration, template, ctx, res, elapsed_ms):
    out = res.get('stdout') or ''
    err = res.get('stderr') or ''
    # BL1: a binary read returns bytes; the audit summary is text.
    if isinstance(out, bytes):
        out = out.decode('utf-8', 'replace')
    if isinstance(err, bytes):
        err = err.decode('utf-8', 'replace')
    tail = scrub_string(out + err)
    tail = tail[-8192:]
    try:
        record_integration_call(
            integration=(integration or {}).get('name') or '',
            tool=template,
            agent=(ctx or {}).get('agent', ''),
            method='SSH',
            path=template,
            status_code=200 if res.get('rc') == 0 else 500,
            latency_ms=elapsed_ms,
            response_summary=tail,
            response_bytes=len(tail.encode('utf-8')),
            truncated=1 if len(tail) >= 8192 else 0,
            outcome='ok' if res.get('rc') == 0 else 'error',
            session_id=(ctx or {}).get('session_id', ''),
            execution_id=(ctx or {}).get('execution_id', ''),
            reason=(ctx or {}).get('reason', ''),
            approval=(ctx or {}).get('approval', ''),
            decided_at=(ctx or {}).get('decided_at', 0),
            decided_by=(ctx or {}).get('decided_by', ''),
            host=target(integration)['ip'])
    except Exception:  # noqa: BLE001 - a record must never break the call
        pass


def run(integration, template, command, ctx=None, stdin=None,
        timeout=TIMEOUT_SHORT, require_pin=False, binary=False):
    """Run one fixed command over SSH; returns {rc, stdout, stderr, timed_out}.

    `binary=True` (BL1) keeps stdout as bytes; F11's read hashes those bytes
    and only decodes them for display.

    TOFU: with no pin, the call runs with `accept-new`, records the host key
    it saw and returns; `require_pin=True` refuses before connecting (writes
    can never establish trust).
    """
    target_ = target(integration)
    key = _key_material(integration)
    pin, _fingerprint = _pin_state(integration)
    if require_pin and not pin:
        raise HaConfigError(
            'ssh_host_unpinned',
            'run ha_config_list once to pin this add-on\'s host key before '
            'writing')
    key_dir = ssh_keys.ensure_key_dir()
    fd, key_path = tempfile.mkstemp(prefix='ha-config-', dir=key_dir)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w') as fh:
        fh.write(key)
    known_hosts = os.path.join(key_dir, 'ha-known-%s' % secrets.token_hex(6))
    if pin:
        hostspec = _known_hosts_host(target_)
        with open(known_hosts, 'w') as fh:
            fh.write('%s %s\n' % (hostspec, pin))
        os.chmod(known_hosts, 0o600)
    else:
        open(known_hosts, 'w').close()
        os.chmod(known_hosts, 0o600)
    started = time.time()
    try:
        res = ssh_client.run_ssh(target_, command, known_hosts, key_path,
                                 timeout, stdin=stdin,
                                 strict_host_key=bool(pin), binary=binary)
        if not pin and res.get('rc') == 0:
            try:
                line = open(known_hosts).read().splitlines()[0]
                fields = line.split()
                if len(fields) >= 3:
                    key_part = ' '.join(fields[-2:])
                    fingerprint = _save_pin(integration, key_part)
                    record_audit_event(
                        'ha_config_pin', target_['ip'],
                        details=('Pinned %s host key %s' % (target_['ip'],
                                                           fingerprint)),
                        actor=(ctx or {}).get('agent', ''))
            except (OSError, IndexError):
                pass
    finally:
        for path in (key_path, known_hosts):
            try:
                os.unlink(path)
            except OSError:
                pass
    _record(integration, template, ctx, res, int((time.time() - started) * 1000))
    err = (res.get('stderr') or '')
    if isinstance(err, bytes):
        err = err.decode('utf-8', 'replace')
    if res.get('rc') != 0:
        if 'Host key verification failed' in err or \
                'REMOTE HOST IDENTIFICATION HAS CHANGED' in err:
            record_audit_event(
                'ha_config_host_key_changed', target_['ip'],
                details=('Refused %s: the pinned host key does not match' %
                         target_['ip']),
                actor=(ctx or {}).get('agent', ''))
            raise HaConfigError('ssh_host_key_changed',
                                'the add-on host key changed since it was pinned')
        if 'Permission denied' in err or 'publickey' in err:
            raise HaConfigError('ssh_auth_failed',
                                'the add-on refused the key (authorized_keys?)')
    return res


def _shell_cmd(script, *args):
    return 'sh -c %s _ %s' % (shlex.quote(script),
                              ' '.join(shlex.quote(str(a)) for a in args))


def _text(value):
    """Decode a binary step's output for a message (BL1)."""
    if isinstance(value, (bytes, bytearray)):
        return value.decode('utf-8', 'replace')
    return value or ''


def _run_checked(integration, template, command, ctx=None, stdin=None,
                 timeout=TIMEOUT_SHORT, require_pin=False, binary=False):
    res = run(integration, template, command, ctx=ctx, stdin=stdin,
              timeout=timeout, require_pin=require_pin, binary=binary)
    if res.get('timed_out'):
        raise HaConfigError('config_timeout', 'the add-on did not answer in time')
    return res


def list_configs(integration, ctx=None):
    """YAML files under /config and /config/packages, with size and mtime."""
    res = _run_checked(integration, 'list', LIST_SCRIPT, ctx=ctx)
    if res.get('rc') != 0:
        raise HaConfigError('config_list_failed',
                            'could not list /config on the add-on')
    out = []
    for line in (res.get('stdout') or '').splitlines():
        parts = line.split(None, 2)
        if len(parts) != 3:
            continue
        size, mtime, path = parts
        if not path.startswith(ROOT + '/'):
            continue
        try:
            out.append({'path': _display_path(path), 'size': int(size),
                        'mtime': int(mtime)})
        except ValueError:
            continue
        if len(out) >= MAX_LIST:
            break
    return out


def verify_path(integration, root_path, ctx=None):
    """Remote verification before a read: realpath equals the expected root
    path and the target is a regular file (a symlink elsewhere is refused)."""
    cmd = ("realpath %s && stat -c '%%F' -- %s"
           % (shlex.quote(root_path), shlex.quote(root_path)))
    res = _run_checked(integration, 'verify', cmd, ctx=ctx)
    if res.get('rc') != 0:
        raise HaConfigError('config_not_found',
                            'the file does not exist on the add-on')
    lines = [l for l in (res.get('stdout') or '').splitlines() if l.strip()]
    if len(lines) < 2:
        raise HaConfigError('config_not_found', 'the file is not readable')
    if lines[0] != root_path:
        raise HaConfigError('config_path_refused',
                            'the path resolves outside the config tree')
    if 'regular file' not in lines[1].lower():
        raise HaConfigError('config_path_refused', 'not a regular file')


def read_config(integration, path, ctx=None):
    """`{content, sha256, size, secrets_masked}`; secrets.yaml is masked.

    BL1: the hash and the size are the remote file's **raw bytes**. The read
    runs with `binary=True`, so CRLF and invalid UTF-8 are not rewritten on
    the way here; only `content` is decoded, with replacement, for display.
    The write script compares `sha256sum` on the add-on, so this is the same
    number.
    """
    root_path = check_read_path(path)
    verify_path(integration, root_path, ctx=ctx)
    cmd = 'head -c %d -- %s' % (MAX_CONTENT + 1, shlex.quote(root_path))
    res = _run_checked(integration, 'read', cmd, ctx=ctx, binary=True)
    if res.get('rc') != 0:
        raise HaConfigError('config_read_failed', 'could not read the file')
    raw = res.get('stdout') or b''
    if isinstance(raw, str):        # a stub that has not been taught bytes
        raw = raw.encode('utf-8')
    size = len(raw)
    if size > MAX_CONTENT:
        raise HaConfigError('config_too_large',
                            'the file is over %d bytes' % MAX_CONTENT)
    import hashlib
    sha = hashlib.sha256(raw).hexdigest()
    text = raw.decode('utf-8', errors='replace')
    masked = root_path.endswith('/' + SECRETS)
    pending = pending_state(integration)
    if pending and pending.get('path') == root_path:
        # U1: a read of the path resolves an unknown state.
        if sha in (pending.get('old_sha'), ):
            _clear_pending(integration)
        elif sha == pending.get('new_sha'):
            _clear_pending(integration)
    return {'path': _display_path(root_path),
            'content': _mask_secrets(text) if masked else text,
            'sha256': sha, 'size': size, 'secrets_masked': masked}


def write_config(integration, path, content, expect_hash, ctx=None,
                 dry_run=False):
    """Validate, then (unless dry_run) one remote script: back up, write,
    check, and restore automatically on a failed check."""
    if _frozen():
        raise HaConfigError('frozen', 'the fleet is frozen; writes are refused')
    if not _pin_state(integration)[0]:
        # The first connection of a new integration must be a read; a write may
        # never be the thing that establishes trust.
        raise HaConfigError(
            'ssh_host_unpinned',
            'run ha_config_list once to pin this add-on\'s host key before '
            'writing')
    info = validate_write(path, content)
    root_path = info['path']
    text = content if isinstance(content, str) else str(content or '')
    if not re.fullmatch(r'[0-9a-f]{64}', str(expect_hash or '')):
        raise HaConfigError('config_hash_required',
                            'pass expect_hash from ha_config_read')
    old = read_config(integration, path, ctx=ctx)
    if old['sha256'] != expect_hash:
        # Also in a dry run: the diff the card shows must be against the bytes
        # the write will replace, or the operator approves a stale diff.
        raise HaConfigError('config_hash_changed',
                            'the file changed since it was read; read it again')
    diff = unified_diff(info['path'], old['content'], text)
    if dry_run:
        return {'dry_run': True, 'path': _display_path(root_path),
                'expect_hash': expect_hash, 'sha256': info['sha256'],
                'size': info['size'], 'risky': info['risky'],
                'includes': info['includes'], 'diff': diff,
                'backup': _backup_name(root_path)}
    pending = pending_state(integration)
    if pending:
        raise HaConfigError(
            'config_state_unknown',
            'a previous write is unresolved for this integration (backup %s); '
            'read the path to resolve it' % (pending.get('backup') or '?'))
    backup = _backup_name(root_path)
    tmp = root_path + '.eshu-tmp-' + secrets.token_hex(8)
    _invalidate_check(integration)   # H5: before the connection
    lock = _integration_lock(integration)
    started = time.time()
    try:
        res = run(integration, 'write',
                  _shell_cmd(WRITE_SCRIPT, root_path, expect_hash, backup, tmp),
                  ctx=ctx, stdin=text, timeout=TIMEOUT_LONG, require_pin=True,
                  binary=True)
    finally:
        lock.release()
    if res.get('timed_out'):
        _mark_unknown(integration, root_path, expect_hash, info['sha256'], backup)
        raise HaConfigError(
            'config_state_unknown',
            'the write timed out; the backup is %s — read the path to resolve'
            % backup)
    rc = int(res.get('rc') or 0)
    if rc == 0:
        check = _text(res.get('stdout')).strip()
        _record_check(integration, True)
        record_audit_event(
            'ha_config_write', target(integration)['ip'],
            details=('Wrote %s (%s -> %s), backup %s' %
                     (_display_path(root_path), expect_hash[:12],
                      info['sha256'][:12], backup)),
            actor=(ctx or {}).get('agent', ''))
        return {'ok': True, 'path': _display_path(root_path),
                'backup': backup, 'sha256': info['sha256'],
                'check': 'valid', 'check_output': check}
    output = (_text(res.get('stdout')) + _text(res.get('stderr'))).strip()
    if rc == 18:
        record_audit_event(
            'ha_config_rollback', target(integration)['ip'],
            details=('ha core check failed after writing %s; restored %s' %
                     (_display_path(root_path), backup)),
            actor=(ctx or {}).get('agent', ''))
        raise HaConfigError(
            'config_check_failed',
            'ha core check failed; the backup was restored. %s' % output[-4000:])
    if 10 <= rc <= 17 or rc == 19:
        raise _write_error(rc)
    _mark_unknown(integration, root_path, expect_hash, info['sha256'], backup)
    raise HaConfigError(
        'config_state_unknown',
        'the write ended with an unknown status (%s); the backup is %s — '
        'read the path to resolve' % (rc, backup))


def restore_config(integration, path, backup, ctx=None):
    """Put one `.eshu-<ts>.bak` back, check, and undo on a failed check."""
    if _frozen():
        raise HaConfigError('frozen', 'the fleet is frozen; restores are refused')
    root_path = check_write_path(path)
    name = str(backup or '').strip()
    if not name.startswith(root_path + '.eshu-') or not BACKUP_RE.match(name):
        raise HaConfigError('config_backup_refused',
                            'the backup must belong to the same path')
    if not re.fullmatch(r'%s\.eshu-[0-9]{10}\.bak'
                        % re.escape(root_path), name):
        raise HaConfigError('config_backup_refused',
                            'the backup name is not one of this file\'s backups')
    pending = pending_state(integration)
    if pending:
        raise HaConfigError(
            'config_state_unknown',
            'a previous write is unresolved for this integration (backup %s); '
            'read the path to resolve it' % (pending.get('backup') or '?'))
    tmp = root_path + '.eshu-tmp-' + secrets.token_hex(8)
    _invalidate_check(integration)
    lock = _integration_lock(integration)
    try:
        res = run(integration, 'restore',
                  _shell_cmd(RESTORE_SCRIPT, root_path, name, tmp),
                  ctx=ctx, timeout=TIMEOUT_LONG, require_pin=True, binary=True)
    finally:
        lock.release()
    if res.get('timed_out'):
        _mark_unknown(integration, root_path, '', '', name)
        raise HaConfigError(
            'config_state_unknown',
            'the restore timed out; the backup is %s — read the path to '
            'resolve' % name)
    rc = int(res.get('rc') or 0)
    if rc == 0:
        _record_check(integration, True)
        record_audit_event(
            'ha_config_restore', target(integration)['ip'],
            details='Restored %s from %s' % (_display_path(root_path), name),
            actor=(ctx or {}).get('agent', ''))
        return {'ok': True, 'path': _display_path(root_path),
                'backup': name, 'check': 'valid',
                'check_output': _text(res.get('stdout')).strip()}
    if rc == 18:
        raise HaConfigError(
            'config_check_failed',
            'ha core check failed after the restore; the previous content was '
            'put back. %s' % (_text(res.get('stdout')) +
                              _text(res.get('stderr')))[-4000:])
    if rc in (12, 19):
        raise HaConfigError('config_backup_refused',
                            'the backup does not exist or is not valid')
    _mark_unknown(integration, root_path, '', '', name)
    raise HaConfigError(
        'config_state_unknown',
        'the restore ended with an unknown status (%s); the backup is %s' %
        (rc, name))


def check_config(integration, ctx=None):
    """Run `ha core check`; a valid result is recorded for F10's restart."""
    res = _run_checked(integration, 'check', 'timeout 100 ha core check 2>&1',
                       ctx=ctx, timeout=TIMEOUT_LONG)
    output = ((res.get('stdout') or '') + (res.get('stderr') or '')).strip()
    valid = res.get('rc') == 0
    _record_check(integration, valid)
    record_audit_event(
        'ha_config_check', target(integration)['ip'],
        details='ha core check: %s' % ('valid' if valid else 'invalid'),
        actor=(ctx or {}).get('agent', ''))
    return {'result': 'valid' if valid else 'invalid',
            'ok': valid, 'output': output}


def _backup_name(root_path):
    return '%s.eshu-%d.bak' % (root_path, _now())


def _mark_unknown(integration, root_path, old_sha, new_sha, backup):
    _set_pending(integration, {'path': root_path, 'old_sha': old_sha,
                               'new_sha': new_sha, 'backup': backup,
                               'at': _now()})
    record_audit_event(
        'ha_config_state_unknown', target(integration)['ip'],
        details=('Write/restore of %s ended without a known result; backup %s' %
                 (_display_path(root_path), backup)),
        actor='')


def _write_error(rc):
    mapping = {
        10: ('config_path_refused', 'the directory is not the HA config tree'),
        11: ('config_path_refused', 'the file is not under the config tree'),
        12: ('config_not_found', 'the file no longer exists'),
        13: ('config_path_refused', 'the path is a symlink or escapes the tree'),
        14: ('config_hash_changed', 'the file changed since it was read'),
        15: ('config_backup_failed', 'the backup could not be written'),
        16: ('config_write_failed', 'the new content could not be written'),
        17: ('config_write_failed', 'the new content could not be moved in'),
        19: ('config_path_refused', 'the path arguments were refused'),
    }
    code, message = mapping.get(rc, ('config_write_failed', 'the write failed'))
    return HaConfigError(code, '%s (remote exit %s)' % (message, rc))

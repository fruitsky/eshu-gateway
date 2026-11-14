"""The one SSH client used by F12's `ssh_exec` and F11's HA config tools.

F12 (gateway commands) and F11 (the Advanced SSH add-on) differ only in the
login user, the port and whether the caller expects an ESHU envelope; the
hardening is identical and lives here so the two paths cannot drift:

* `ssh -F /dev/null` (no user config), the target, and a fixed option set:
  `BatchMode=yes`, `IdentitiesOnly=yes`, `StrictHostKeyChecking=yes`,
  `UserKnownHostsFile=<pin file>`, `GlobalKnownHostsFile=/dev/null`,
  `ConnectTimeout=10`, `ServerAliveInterval=15`, `LogLevel=ERROR`;
* a private key file, never an agent or a password;
* no local shell (`subprocess` argv, never `shell=True`);
* the local process group is killed on timeout (`start_new_session` +
  `killpg`), which bounds how long the dashboard waits. It does not stop the
  remote command; each caller documents its own remote bound.
"""
import os
import signal
import subprocess

from core import ssh_keys


def build_argv(target: dict, command_line: str, known_hosts: str,
               key_path: str, strict_host_key: bool = True) -> list:
    """The fixed ssh argv for one call.

    `target` carries `ip` (or `host`), optional `port` (default 22) and
    optional `user`. The host is a registered IP/name the caller validated;
    it is never agent text.
    """
    host = str(target.get('ip') or target.get('host') or '')
    user = str(target.get('user') or 'eshu-gateway')
    try:
        port = int(target.get('port') or 22)
    except (TypeError, ValueError):
        port = 22
    argv = [
        'ssh',
        '-F', '/dev/null',
        '-i', key_path,
        '-l', user,
    ]
    if port != 22:
        argv += ['-p', str(port)]
    argv += [
        '-o', 'BatchMode=yes',
        '-o', 'IdentitiesOnly=yes',
        '-o', 'StrictHostKeyChecking=yes' if strict_host_key else 'StrictHostKeyChecking=accept-new',
        '-o', 'UserKnownHostsFile=%s' % known_hosts,
        '-o', 'GlobalKnownHostsFile=/dev/null',
        '-o', 'ConnectTimeout=10',
        '-o', 'ServerAliveInterval=15',
        '-o', 'LogLevel=ERROR',
        host,
        command_line,
    ]
    return argv


def run_ssh(target: dict, command_line: str, known_hosts: str, key_path: str,
            timeout: int, stdin: str = None,
            strict_host_key: bool = True, binary: bool = False) -> dict:
    """Run one ssh call without a shell.

    `stdin` is written to the remote command's stdin (F11's write path) and
    never appears in argv. `strict_host_key=False` is only for the first
    connection of a new integration (TOFU): it lets ssh record the host key it
    sees, which the caller then pins.

    `binary=True` returns `stdout`/`stderr` as bytes and sends `stdin` as
    UTF-8 bytes. F11's read path uses it: text mode would translate CRLF and
    replace invalid UTF-8, so the hash of a file would not be the hash of the
    bytes `sha256sum` sees on the add-on. F12 keeps the text default.
    """
    argv = build_argv(target, command_line, known_hosts, key_path,
                      strict_host_key=strict_host_key)
    env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LANG': 'C',
           'HOME': ssh_keys.key_dir()}
    empty = b'' if binary else ''
    popen = {'stdin': subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
             'stdout': subprocess.PIPE, 'stderr': subprocess.PIPE,
             'start_new_session': True, 'env': env}
    if not binary:
        popen.update(text=True, errors='replace')
    try:
        proc = subprocess.Popen(argv, **popen)
    except OSError as exc:
        return {'rc': 127, 'stdout': empty,
                'stderr': str(exc) if not binary else str(exc).encode('utf-8'),
                'timed_out': False}
    if binary and stdin is not None and not isinstance(stdin, (bytes, bytearray)):
        stdin = str(stdin).encode('utf-8')
    try:
        if stdin is None:
            out, err = proc.communicate(timeout=timeout)
        else:
            out, err = proc.communicate(input=stdin, timeout=timeout)
        return {'rc': proc.returncode, 'stdout': out or empty,
                'stderr': err or empty, 'timed_out': False}
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            out, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            out, err = empty, empty
        return {'rc': 124, 'stdout': out or empty, 'stderr': err or empty,
                'timed_out': True}

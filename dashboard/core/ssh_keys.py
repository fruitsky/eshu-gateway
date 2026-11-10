"""F12: the dashboard's service key and the per-gateway host-key pin.

The dashboard holds one ed25519 service key (`dashboard_ed25519`) that reaches
every gateway that enrolled it. It is a service key, not a user's: no
passphrase, `0600` in a `0700` directory owned by the service user, and the
runner refuses to start a call when either mode or owner is wrong.

Host keys are pinned per gateway from the gateway's token-authenticated
registration (never scanned over the network). The pin stores the public key
*line*: `known_hosts` needs the key blob, and a SHA256 fingerprint alone
cannot be turned back into an entry. The fingerprint is still what the UI and
the audit trail show.
"""
import base64
import hashlib
import os
import subprocess

DEFAULT_KEY_DIR = '/var/lib/eshu/ssh'
KEY_NAME = 'dashboard_ed25519'
KEY_COMMENT = 'eshu-dashboard'

# The key types sshd may present. Anything else is not something we pin.
HOST_KEY_TYPES = ('ssh-ed25519', 'ssh-rsa', 'ecdsa-sha2-nistp256',
                  'ecdsa-sha2-nistp384', 'ecdsa-sha2-nistp521',
                  'sk-ssh-ed25519@openssh.com',
                  'sk-ecdsa-sha2-nistp256@openssh.com')

MAX_KEY_LINE = 1024


class KeyError_(Exception):
    """A key problem the caller turns into a refusal (never a traceback)."""

    def __init__(self, code, message=''):
        super().__init__(code)
        self.code = code
        self.message = message or code


def key_dir() -> str:
    return os.environ.get('ESHU_SSH_KEY_DIR') or DEFAULT_KEY_DIR


def private_key_path() -> str:
    return os.path.join(key_dir(), KEY_NAME)


def public_key_path() -> str:
    return private_key_path() + '.pub'


def ensure_key_dir(path: str = None) -> str:
    """Create the private key dir `0700` and refuse a foreign mode/owner."""
    path = path or key_dir()
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
    except OSError as exc:
        raise KeyError_('ssh_key_dir', str(exc))
    st = os.stat(path)
    if st.st_mode & 0o777 != 0o700:
        raise KeyError_('ssh_key_dir_mode', 'the key directory must be 0700')
    if st.st_uid != os.getuid():
        raise KeyError_('ssh_key_dir_owner', 'the key directory must be owned by the service user')
    return path


def ensure_key_pair() -> str:
    """Generate the service key once, then return the private key path."""
    directory = ensure_key_dir()
    key = private_key_path()
    if not os.path.exists(key):
        try:
            subprocess.run(
                ['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', KEY_COMMENT,
                 '-f', key],
                check=True, capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            raise KeyError_('ssh_key_generate', str(exc))
        os.chmod(key, 0o600)
    st = os.stat(key)
    if st.st_mode & 0o777 != 0o600:
        raise KeyError_('ssh_key_mode', 'the key must be 0600')
    if st.st_uid != os.getuid():
        raise KeyError_('ssh_key_owner', 'the key must be owned by the service user')
    return key


def public_key_line() -> str:
    ensure_key_pair()
    try:
        with open(public_key_path(), 'r', encoding='utf-8') as handle:
            line = handle.read().strip()
    except OSError as exc:
        raise KeyError_('ssh_key_public', str(exc))
    if parse_public_key(line) is None:
        raise KeyError_('ssh_key_public', 'the public key does not parse')
    return line


def parse_public_key(line: str):
    """(type, blob bytes, comment) for a well-formed public key line."""
    text = (line or '').strip()
    if not text or '\n' in text or '\r' in text or len(text) > MAX_KEY_LINE:
        return None
    parts = text.split()
    if len(parts) < 2:
        return None
    key_type, blob_b64 = parts[0], parts[1]
    if key_type not in HOST_KEY_TYPES:
        return None
    try:
        blob = base64.b64decode(blob_b64, validate=True)
    except (ValueError, TypeError):
        return None
    if not blob or len(blob) > 512:
        return None
    comment = ' '.join(parts[2:]) if len(parts) > 2 else ''
    return key_type, blob, comment


def fingerprint(line: str) -> str:
    """`SHA256:…`, the same shape `ssh-keygen -lf` prints."""
    parsed = parse_public_key(line)
    if parsed is None:
        return ''
    digest = hashlib.sha256(parsed[1]).digest()
    return 'SHA256:' + base64.b64encode(digest).decode('ascii').rstrip('=')


def known_hosts_path(ip: str) -> str:
    safe = (ip or '').replace('/', '_').replace('..', '_')
    return os.path.join(key_dir(), f'known_hosts.{safe}')


def write_known_hosts(ip: str, key_line: str) -> str:
    """Write (or replace) the one-entry pin file for a gateway, mode 0600."""
    path = known_hosts_path(ip)
    ensure_key_dir()
    parsed = parse_public_key(key_line)
    if parsed is None:
        raise KeyError_('ssh_host_unpinned')
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as handle:
        handle.write(f"{ip} {parsed[0]} {key_line.split()[1]}\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path

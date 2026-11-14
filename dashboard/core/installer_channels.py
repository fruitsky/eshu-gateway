"""X7: the three installer channels — Build, Edge and Fleet.

* **Build** is `dashboard/eshu-gateway-install.sh`, the generated file that is
  committed to the repo. A dashboard deploy changes it, and nothing else.
* **Edge** is what dev-mode gateways install. It changes only when the
  operator presses Seed Edge (a byte copy of Build).
* **Fleet** is what every other gateway installs: enrollment, updates,
  uninstall and rollback all download it. It changes only on Deploy to Fleet
  (a byte copy of Edge) or Rollback (the previous Fleet copy).

Edge and Fleet are runtime state owned by the dashboard, kept outside the
static mount and outside git (`dashboard/installer-channels/`, ignored; the
`ESHU_INSTALLER_CHANNELS` environment variable overrides the directory, which
is how the tests isolate it). Before X7 both were committed copies, so every
deploy overwrote them: Build always equalled Edge, and the fleet installer
changed on a deploy without anyone promoting it.

Every write is atomic (a temp file in the same directory, fsync, rename), so a
gateway never downloads a half-written installer. A channel that is missing
after initialisation is reported as missing (the endpoints answer 503); it is
never silently rebuilt from Build, because that would bypass the promote gate.
"""
import hashlib
import os
import tempfile
import time

EDGE = 'edge'
FLEET = 'fleet'
FLEET_PREV = 'fleet.prev'
_FILES = {EDGE: 'edge.sh', FLEET: 'fleet.sh', FLEET_PREV: 'fleet.prev.sh'}

# Set once, in the meta table, when the channels were first created. After
# that a missing channel file is an error, not something to re-create.
INIT_MARKER = 'installer_channels_v1'

_DASHBOARD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ChannelMissing(Exception):
    """A channel file is absent after initialisation (serve 503)."""


def build_path() -> str:
    return os.path.join(_DASHBOARD_DIR, 'eshu-gateway-install.sh')


def channel_dir() -> str:
    return (os.environ.get('ESHU_INSTALLER_CHANNELS')
            or os.path.join(_DASHBOARD_DIR, 'installer-channels'))


def channel_path(channel: str) -> str:
    return os.path.join(channel_dir(), _FILES[channel])


def _legacy_paths() -> dict:
    """Where the pre-X7 copies lived (inside the static mount). The first
    start deletes them; their contents are never used."""
    static = os.path.join(_DASHBOARD_DIR, 'static')
    return {
        FLEET: os.path.join(static, 'eshu-gateway-install.sh'),
        EDGE: os.path.join(static, 'dev', 'eshu-gateway-install.sh'),
        FLEET_PREV: os.path.join(static, 'eshu-gateway-install.sh.bak'),
    }


def file_hash(path: str):
    """First 12 hex chars of the SHA-256, or None when the file is missing."""
    try:
        with open(path, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()[:12]
    except OSError:
        return None


def _ensure_dir() -> str:
    """The channel directory, 0700 even when it already existed (makedirs
    applies the mode only when it creates the directory)."""
    path = channel_dir()
    os.makedirs(path, mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def _atomic_write(dst: str, data: bytes) -> None:
    _ensure_dir()
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dst),
                               prefix='.' + os.path.basename(dst) + '.')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, dst)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_bytes(path: str):
    try:
        with open(path, 'rb') as f:
            return f.read()
    except OSError:
        return None


def _initialised() -> bool:
    from db.policies import get_meta
    return bool(get_meta(INIT_MARKER))


def ensure_initialised() -> None:
    """Create Edge and Fleet once, from Build, and remove the pre-X7 copies.

    Runs at startup and lazily before any channel access. Both channels start
    as Build: the committed copies were byte-identical to it, and Build is
    what /api/gateway-script served before X7, so the fleet sees no change.
    A pre-X7 copy still on disk could only be one changed at runtime, so it is
    never used (security, X7 question 3).

    BP1: no previous-Fleet copy is created. The old `.bak` was an installer of
    unknown age, possibly older than this week's security fixes; making it the
    Rollback target would let the first Rollback downgrade the fleet. Rollback
    stays unavailable until the first Deploy to Fleet. The pre-X7 files are
    deleted, because the static mount would otherwise keep serving them.
    """
    if _initialised():
        return
    from db.audit import record_audit_event
    from db.misc import get_deployed_golden_hash, set_deployed_golden_hash
    from db.policies import set_meta
    _ensure_dir()
    build = _read_bytes(build_path())
    notes = []
    for channel in (FLEET, EDGE):
        dst = channel_path(channel)
        if os.path.exists(dst):
            notes.append('%s kept' % channel)
            continue
        if build is None:
            notes.append('%s not created (no Build)' % channel)
            continue
        _atomic_write(dst, build)
        notes.append('%s from Build (%s)' % (channel, file_hash(dst)))
    removed = []
    for path in _legacy_paths().values():
        try:
            os.remove(path)
            removed.append(os.path.basename(os.path.dirname(path)) + '/'
                           + os.path.basename(path))
        except FileNotFoundError:
            pass
        except OSError as exc:
            notes.append('could not remove %s: %s' % (os.path.basename(path), exc))
    dev_dir = os.path.dirname(_legacy_paths()[EDGE])
    try:
        os.rmdir(dev_dir)
    except OSError:
        pass
    if removed:
        notes.append('removed pre-X7 copies: ' + ', '.join(removed))
    notes.append('no previous Fleet: Rollback is available after the first '
                 'Deploy to Fleet')
    fleet_hash = file_hash(channel_path(FLEET))
    deployed = get_deployed_golden_hash()
    if fleet_hash and deployed and deployed != fleet_hash:
        notes.append('Fleet differs from the last recorded promote (%s); '
                     'it is the installer gateways were actually being served'
                     % deployed)
    if fleet_hash:
        set_deployed_golden_hash(fleet_hash)
    set_meta(INIT_MARKER, str(int(time.time())))
    record_audit_event('installer_channels_initialised',
                       details='Installer channels created: ' + '; '.join(notes))


def read(channel: str) -> bytes:
    """The channel's bytes; raises ChannelMissing when absent."""
    ensure_initialised()
    data = _read_bytes(channel_path(channel))
    if data is None:
        raise ChannelMissing(channel)
    return data


def channel_hash(channel: str):
    ensure_initialised()
    return file_hash(channel_path(channel))


def build_hash():
    return file_hash(build_path())


def seed_edge() -> str:
    """Edge := Build. Returns the new Edge hash."""
    ensure_initialised()
    data = _read_bytes(build_path())
    if data is None:
        raise ChannelMissing('build')
    _atomic_write(channel_path(EDGE), data)
    return file_hash(channel_path(EDGE))


def promote_edge() -> str:
    """Fleet.prev := Fleet, then Fleet := Edge. Returns the new Fleet hash."""
    edge = read(EDGE)
    current = _read_bytes(channel_path(FLEET))
    if current is not None:
        _atomic_write(channel_path(FLEET_PREV), current)
    _atomic_write(channel_path(FLEET), edge)
    return file_hash(channel_path(FLEET))


def rollback_fleet() -> str:
    """Fleet := Fleet.prev. Returns the new Fleet hash."""
    prev = read(FLEET_PREV)
    _atomic_write(channel_path(FLEET), prev)
    return file_hash(channel_path(FLEET))


def has_previous() -> bool:
    ensure_initialised()
    return os.path.exists(channel_path(FLEET_PREV))

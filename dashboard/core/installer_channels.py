"""X9: gateway release generations — installer + the two gated scripts.

X7 made the installer a real channel (Build → Edge → Fleet). X9 extends a
channel from one file to a **generation**: the installer *and* the two files
that gateways run as root (`approved_windows.sh`, `fleet-runner.sh`), moved
and rolled back as one set.

* **Build** is the committed, generated installer plus
  `dashboard/gateway-files/`. A dashboard deploy changes it.
* **Edge** is what dev-mode gateways install and fetch. It changes only on
  Seed Edge, which copies Build's three files into one generation.
* **Fleet** is what every other gateway installs and fetches. It changes only
  on Deploy to Fleet (the whole Edge generation) or Rollback (the previous
  Fleet generation).

Layout under `dashboard/installer-channels/` (gitignored;
`ESHU_INSTALLER_CHANNELS` overrides it, which is how the tests isolate it):

    edge, fleet, fleet.prev        pointer files: a generation id
    gen-<id>/installer.sh
    gen-<id>/approved_windows.sh
    gen-<id>/fleet-runner.sh

A generation directory is written to a temporary name and renamed into place,
so a partial copy is never visible. A pointer is replaced with an atomic
rename only after all three files exist. A channel whose generation is
incomplete is reported missing (503); it never falls back to Build or to the
other channel. This is BU1: the installer and the files cannot be mixed
across generations.
"""
import hashlib
import os
import shutil
import tempfile
import time

EDGE = 'edge'
FLEET = 'fleet'
FLEET_PREV = 'fleet.prev'

# The three files of one generation, in a fixed order. The first is the
# installer; the other two are fetched by the 1.4 poller.
INSTALLER = 'installer.sh'
WINDOWS = 'approved_windows.sh'
RUNNER = 'fleet-runner.sh'
GEN_FILES = (INSTALLER, WINDOWS, RUNNER)

# X7 flat files: a deployment upgrading from X7 migrates them into a
# generation; they are deleted afterwards.
_LEGACY_FLAT = {EDGE: 'edge.sh', FLEET: 'fleet.sh', FLEET_PREV: 'fleet.prev.sh'}

INIT_MARKER = 'installer_generations_v1'

_DASHBOARD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ChannelMissing(Exception):
    """A channel generation is absent or incomplete (serve 503)."""


def build_path() -> str:
    return os.path.join(_DASHBOARD_DIR, 'eshu-gateway-install.sh')


def gateway_files_dir() -> str:
    """The Build copies of the two root-run scripts (moved out of /static)."""
    return os.path.join(_DASHBOARD_DIR, 'gateway-files')


def channel_dir() -> str:
    return (os.environ.get('ESHU_INSTALLER_CHANNELS')
            or os.path.join(_DASHBOARD_DIR, 'installer-channels'))


def file_hash(path: str):
    """First 12 hex chars of the SHA-256, or None when the file is missing."""
    try:
        with open(path, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()[:12]
    except OSError:
        return None


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def _ensure_dir() -> str:
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


def _read_pointer(channel: str):
    data = _read_bytes(os.path.join(channel_dir(), channel))
    if not data:
        return None
    gen = data.decode('ascii', 'replace').strip()
    return gen if _valid_gen(gen) else None


def _valid_gen(gen: str) -> bool:
    return bool(gen) and all(c in '0123456789abcdef' for c in gen) and len(gen) == 12


def _write_pointer(channel: str, gen: str) -> None:
    _atomic_write(os.path.join(channel_dir(), channel), (gen + '\n').encode())


def _gen_dir(gen: str) -> str:
    return os.path.join(channel_dir(), 'gen-' + gen)


def _gen_hashes(gen: str):
    """The three file hashes, or None when any file is missing."""
    hashes = {}
    for name in GEN_FILES:
        h = file_hash(os.path.join(_gen_dir(gen), name))
        if h is None:
            return None
        hashes[name] = h
    return hashes


def generation_id(files: dict) -> str:
    """One id for a set of three files, from their bytes (order fixed)."""
    h = hashlib.sha256()
    for name in GEN_FILES:
        data = files[name]
        h.update(name.encode('ascii') + b'\0')
        h.update(str(len(data)).encode('ascii') + b'\0')
        h.update(data)
    return h.hexdigest()[:12]


def _write_generation(files: dict) -> str:
    """Write a generation atomically; return its id. Idempotent."""
    gen = generation_id(files)
    _ensure_dir()
    dst = _gen_dir(gen)
    if _gen_hashes(gen):
        return gen
    tmp = dst + '.tmp-%d' % os.getpid()
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, mode=0o700)
    try:
        for name in GEN_FILES:
            with open(os.path.join(tmp, name), 'wb') as f:
                f.write(files[name])
                f.flush()
                os.fsync(f.fileno())
            os.chmod(os.path.join(tmp, name), 0o644)
        try:
            os.replace(tmp, dst)
        except OSError:
            # Another writer won the race; its generation is identical.
            if not _gen_hashes(gen):
                raise
            shutil.rmtree(tmp, ignore_errors=True)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return gen


def _gateway_file(name: str):
    return _read_bytes(os.path.join(gateway_files_dir(), name))


def build_generation() -> dict:
    """The Build files, or ChannelMissing when any of them is absent."""
    installer = _read_bytes(build_path())
    windows = _gateway_file(WINDOWS)
    runner = _gateway_file(RUNNER)
    if installer is None or windows is None or runner is None:
        raise ChannelMissing('build')
    return {INSTALLER: installer, WINDOWS: windows, RUNNER: runner}


def _legacy_paths() -> dict:
    """Where the pre-X7 copies lived (inside the static mount). The first
    start deletes them; their contents are never used."""
    static = os.path.join(_DASHBOARD_DIR, 'static')
    return {
        FLEET: os.path.join(static, 'eshu-gateway-install.sh'),
        EDGE: os.path.join(static, 'dev', 'eshu-gateway-install.sh'),
        FLEET_PREV: os.path.join(static, 'eshu-gateway-install.sh.bak'),
    }


def _migrate_flat() -> dict:
    """X7 flat channel files, if any. Read before they are deleted."""
    out = {}
    for channel, name in _LEGACY_FLAT.items():
        data = _read_bytes(os.path.join(channel_dir(), name))
        if data is not None:
            out[channel] = data
    return out


def _initialised() -> bool:
    from db.policies import get_meta
    return bool(get_meta(INIT_MARKER))


def ensure_initialised() -> None:
    """Create Edge and Fleet once, from Build or from the X7 flat copies.

    BP1 still holds: no previous-Fleet generation is created on migration, so
    Rollback is unavailable until the first Deploy to Fleet. Pre-X7 files are
    deleted because the static mount would otherwise keep serving them. The
    X7 flat installers are paired with the current gateway files — those are
    the files the fleet was already downloading from `/static` — and then
    removed. Every generated channel is one complete generation.
    """
    if _initialised():
        return
    from db.audit import record_audit_event
    from db.misc import get_deployed_golden_hash, set_deployed_golden_hash
    from db.policies import get_meta, set_meta
    _ensure_dir()
    notes = []
    try:
        build = build_generation()
    except ChannelMissing:
        build = None
    flat = _migrate_flat()

    def _make(channel):
        if channel in flat:
            gen = _write_generation({INSTALLER: flat[channel],
                                     WINDOWS: build[WINDOWS],
                                     RUNNER: build[RUNNER]}) if build else None
            if gen:
                notes.append('%s migrated from the X7 %s (%s)'
                             % (channel, _LEGACY_FLAT[channel], gen))
                return gen
        if build is None:
            return None
        gen = _write_generation(build)
        notes.append('%s from Build (%s)' % (channel, gen))
        return gen

    edge_gen = _make(EDGE)
    fleet_gen = _make(FLEET)
    if edge_gen:
        _write_pointer(EDGE, edge_gen)
    if fleet_gen:
        _write_pointer(FLEET, fleet_gen)

    removed = []
    for path in list(_legacy_paths().values()) + [
            os.path.join(channel_dir(), name) for name in _LEGACY_FLAT.values()]:
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
    fleet_gen = _read_pointer(FLEET)
    fleet_hashes = _gen_hashes(fleet_gen) if fleet_gen else None
    fleet_hash = fleet_hashes[INSTALLER] if fleet_hashes else None
    deployed = get_deployed_golden_hash()
    if fleet_hash and deployed and deployed != fleet_hash:
        notes.append('Fleet set to Build; the last recorded promote was %s'
                     % deployed)
    if fleet_hash:
        set_deployed_golden_hash(fleet_hash)
    if not get_meta('legacy_1_3_since'):
        set_meta('legacy_1_3_since', str(int(time.time())))
    set_meta(INIT_MARKER, str(int(time.time())))
    record_audit_event('installer_channels_initialised',
                       details='Installer channels created: ' + '; '.join(notes))


def channel_generation(channel: str):
    """The generation id a channel points at, or None (missing/incomplete)."""
    ensure_initialised()
    gen = _read_pointer(channel)
    if not gen or not _gen_hashes(gen):
        return None
    return gen


def channel_path(channel: str) -> str:
    """The installer path of the channel's current generation."""
    gen = channel_generation(channel)
    if not gen:
        raise ChannelMissing(channel)
    return os.path.join(_gen_dir(gen), INSTALLER)


def channel_info(channel: str):
    """{generation, installer, windows, runner} hashes, or None."""
    gen = channel_generation(channel)
    if not gen:
        return None
    hashes = _gen_hashes(gen)
    if not hashes:
        return None
    return {'generation': gen, 'installer': hashes[INSTALLER],
            'windows': hashes[WINDOWS], 'runner': hashes[RUNNER]}


def channel_file_hash(channel: str, name: str):
    info = channel_info(channel)
    if not info:
        return None
    return info[{INSTALLER: 'installer', WINDOWS: 'windows',
                 RUNNER: 'runner'}[name]]


def channel_hash(channel: str):
    """The installer hash of the channel (the X7 API)."""
    return channel_file_hash(channel, INSTALLER)


def build_hash():
    return file_hash(build_path())


def read_generation(gen: str) -> dict:
    files = {}
    for name in GEN_FILES:
        data = _read_bytes(os.path.join(_gen_dir(gen), name))
        if data is None:
            raise ChannelMissing(gen)
        files[name] = data
    return files


def read(channel: str) -> bytes:
    """The channel's installer bytes; raises ChannelMissing when absent."""
    gen = channel_generation(channel)
    if not gen:
        raise ChannelMissing(channel)
    data = _read_bytes(os.path.join(_gen_dir(gen), INSTALLER))
    if data is None:
        raise ChannelMissing(channel)
    return data


def read_file(channel: str, name: str) -> bytes:
    """One of the channel's gated files; raises ChannelMissing when absent."""
    if name not in (WINDOWS, RUNNER):
        raise ChannelMissing(name)
    gen = channel_generation(channel)
    if not gen:
        raise ChannelMissing(channel)
    data = _read_bytes(os.path.join(_gen_dir(gen), name))
    if data is None:
        raise ChannelMissing(channel)
    return data


def seed_edge() -> dict:
    """Edge := Build's whole generation. Returns its hashes."""
    ensure_initialised()
    gen = _write_generation(build_generation())
    _write_pointer(EDGE, gen)
    return channel_info(EDGE)


def promote_edge() -> dict:
    """Fleet.prev := Fleet; Fleet := Edge (whole generations)."""
    ensure_initialised()
    edge = channel_generation(EDGE)
    if not edge:
        raise ChannelMissing(EDGE)
    current = channel_generation(FLEET)
    if current:
        _write_pointer(FLEET_PREV, current)
    _write_pointer(FLEET, edge)
    return channel_info(FLEET)


def rollback_fleet() -> dict:
    """Fleet := the generation Fleet.prev points at (whole set)."""
    ensure_initialised()
    prev = _read_pointer(FLEET_PREV)
    if not prev or not _gen_hashes(prev):
        raise ChannelMissing(FLEET_PREV)
    _write_pointer(FLEET, prev)
    return channel_info(FLEET)


def has_previous() -> bool:
    ensure_initialised()
    prev = _read_pointer(FLEET_PREV)
    return bool(prev and _gen_hashes(prev))

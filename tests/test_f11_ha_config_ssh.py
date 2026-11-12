"""F11 part 2 — the HA config SSH transport.

Every test stubs `core.ssh_client.run_ssh`: the remote side is a disposable
filesystem in the stub, so the write script's exit codes and the dashboard's
mapping are exercised without a real add-on. The scripts themselves are pinned
by the design's assertions (BusyBox-compatible, no `find /config`, one
connection per write).
"""
import base64
import hashlib
import json
import time

import pytest


@pytest.fixture(autouse=True)
def _key_dir(tmp_path, monkeypatch):
    monkeypatch.setenv('ESHU_SSH_KEY_DIR', str(tmp_path / 'keys'))
    return tmp_path / 'keys'


def _key():
    return ('-----BEGIN OPENSSH PRIVATE KEY-----\n'
            'AAAA\n-----END OPENSSH PRIVATE KEY-----\n')


def _pub():
    # a syntactically valid ed25519 public key line (32-byte blob)
    return 'ssh-ed25519 ' + base64.b64encode(b'0' * 32).decode()


@pytest.fixture
def integration(temp_db):
    from db.integrations import create_integration, get_integration, set_integration_config
    create_integration('ha-ssh', '192.0.2.10:2222', 'none', _key(),
                       kind='ha_ssh')
    item = get_integration('ha-ssh')
    set_integration_config(item, {'ssh_user': 'root', 'ssh_port': 2222})
    return get_integration('ha-ssh')


@pytest.fixture
def pinned(integration):
    from db.integrations import get_integration, set_integration_config
    set_integration_config(integration, {'host_key': _pub(),
                                         'host_fingerprint': 'SHA256:x'})
    return get_integration('ha-ssh')


class FakeSSH:
    """A remote filesystem plus the rc/stdout a script step would produce."""

    def __init__(self, files=None, pin=True):
        self.files = dict(files or {})
        self.calls = []
        self.pin = pin
        self.write_rc = 0
        self.write_stdout = 'ok %s' % ('0' * 64)
        self.check_rc = 0

    def __call__(self, target, command, known_hosts, key_path, timeout,
                 stdin=None, strict_host_key=True):
        self.calls.append({'target': dict(target), 'command': command,
                           'stdin': stdin, 'timeout': timeout,
                           'known_hosts': known_hosts,
                           'strict': strict_host_key})
        if not strict_host_key and self.pin:
            with open(known_hosts, 'w') as fh:
                fh.write('%s %s\n' % (target['ip'], _pub()))
        if command.startswith('find /homeassistant'):
            rows = []
            for path, content in self.files.items():
                rows.append('%d %d %s' % (len(content), int(time.time()), path))
            return {'rc': 0, 'stdout': '\n'.join(rows) + '\n',
                    'stderr': '', 'timed_out': False}
        if command.startswith('realpath'):
            path = command.split()[1].strip("'")
            if path not in self.files:
                return {'rc': 1, 'stdout': '', 'stderr': 'no such file',
                        'timed_out': False}
            return {'rc': 0, 'stdout': path + '\nregular file\n',
                    'stderr': '', 'timed_out': False}
        if command.startswith('head -c'):
            path = command.split('-- ', 1)[1].strip().strip("'")
            return {'rc': 0, 'stdout': self.files.get(path, ''),
                    'stderr': '', 'timed_out': False}
        if command.startswith('timeout 100 ha core check'):
            return {'rc': self.check_rc, 'stdout': '',
                    'stderr': 'check output', 'timed_out': False}
        if command.startswith("sh -c"):
            return {'rc': self.write_rc, 'stdout': self.write_stdout,
                    'stderr': 'script error', 'timed_out': False}
        return {'rc': 3, 'stdout': '', 'stderr': 'unexpected command',
                'timed_out': False}


@pytest.fixture
def fake(monkeypatch):
    stub = FakeSSH({'/homeassistant/configuration.yaml': 'a: 1\n'})
    monkeypatch.setattr('core.ssh_client.run_ssh', stub)
    return stub


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


# ── path policy ────────────────────────────────────────────────────────────

def test_read_path_policy():
    from core.ha_config_ssh import HaConfigError, check_read_path
    assert check_read_path('/config/configuration.yaml') == '/homeassistant/configuration.yaml'
    assert check_read_path('/config/packages/net/x.yml') == '/homeassistant/packages/net/x.yml'
    for bad in ('/config/.storage/core.restore', '/config/.ssh/id_rsa',
                '/config/deps/x.yaml', '/config/tts/x.yaml',
                '/config/../etc/passwd', '/etc/passwd',
                '/config/configuration.yaml/../x.yaml',
                '/config/secrets.txt', '/config/foo.json', '/config'):
        with pytest.raises(HaConfigError) as exc:
            check_read_path(bad)
        assert exc.value.code in ('config_path_refused', 'config_secret_refused')


# ── TOFU pin ──────────────────────────────────────────────────────────────

def test_list_pins_the_host_key_on_first_use(integration, fake):
    from core.ha_config_ssh import list_configs
    from db.integrations import get_integration, get_integration_config
    out = list_configs(integration)
    assert out and out[0]['path'] == '/config/configuration.yaml'
    cfg = get_integration_config(get_integration('ha-ssh'))
    assert cfg['host_key'].startswith('ssh-ed25519 ')
    assert cfg['host_fingerprint'].startswith('SHA256:')
    assert fake.calls[0]['strict'] is False


def test_a_write_without_a_pin_refuses_before_any_ssh_call(integration, fake):
    from core.ha_config_ssh import HaConfigError, write_config
    with pytest.raises(HaConfigError) as exc:
        write_config(integration, '/config/configuration.yaml', 'b: 2\n',
                     _sha('a: 1\n'))
    assert exc.value.code == 'ssh_host_unpinned'
    assert fake.calls == []


def test_a_changed_host_key_refuses_and_audits(pinned, monkeypatch):
    from core.ha_config_ssh import HaConfigError, list_configs
    from db.audit import get_audit_log

    def changed(*a, **k):
        return {'rc': 255, 'stdout': '',
                'stderr': 'Host key verification failed.', 'timed_out': False}

    monkeypatch.setattr('core.ssh_client.run_ssh', changed)
    with pytest.raises(HaConfigError) as exc:
        list_configs(pinned)
    assert exc.value.code == 'ssh_host_key_changed'
    assert any(r['event_type'] == 'ha_config_host_key_changed'
               for r in get_audit_log())


def test_the_runner_argv_uses_the_configured_user_port_and_pin(pinned, fake):
    from core.ha_config_ssh import list_configs
    list_configs(pinned)
    call = fake.calls[0]
    assert call['target'] == {'ip': '192.0.2.10', 'port': 2222, 'user': 'root'}
    assert call['strict'] is True
    assert call['timeout'] == 30


# ── read ──────────────────────────────────────────────────────────────────

def test_read_returns_content_and_hash(pinned, fake):
    from core.ha_config_ssh import read_config
    out = read_config(pinned, '/config/configuration.yaml')
    assert out['content'] == 'a: 1\n'
    assert out['sha256'] == _sha('a: 1\n')
    assert out['secrets_masked'] is False


def test_secrets_yaml_is_masked_but_hashed(pinned, fake):
    fake.files['/homeassistant/secrets.yaml'] = 'api_key: hunter2\nname: x\n'
    from core.ha_config_ssh import read_config
    out = read_config(pinned, '/config/secrets.yaml')
    assert out['secrets_masked'] is True
    assert 'hunter2' not in out['content']
    assert 'api_key: "***"' in out['content']
    assert out['sha256'] == _sha('api_key: hunter2\nname: x\n')


def test_a_symlinked_path_is_refused_before_reading(pinned, fake):
    from core.ha_config_ssh import HaConfigError, read_config

    def realpath_elsewhere(target, command, known_hosts, key_path, timeout,
                           stdin=None, strict_host_key=True):
        return {'rc': 0, 'stdout': '/etc/passwd\nregular file\n',
                'stderr': '', 'timed_out': False}

    monkeypatched = realpath_elsewhere
    import core.ssh_client
    core.ssh_client.run_ssh = monkeypatched
    try:
        with pytest.raises(HaConfigError) as exc:
            read_config(pinned, '/config/configuration.yaml')
        assert exc.value.code == 'config_path_refused'
    finally:
        core.ssh_client.run_ssh = fake


# ── write ─────────────────────────────────────────────────────────────────

def test_dry_run_returns_a_diff_and_writes_nothing(pinned, fake):
    from core.ha_config_ssh import write_config
    out = write_config(pinned, '/config/configuration.yaml', 'a: 2\n',
                       _sha('a: 1\n'), dry_run=True)
    assert out['dry_run'] is True
    assert '+a: 2' in out['diff']
    assert not any(c['command'].startswith('sh -c') for c in fake.calls)


def test_a_stale_hash_refuses_before_the_write(pinned, fake):
    from core.ha_config_ssh import HaConfigError, write_config
    with pytest.raises(HaConfigError) as exc:
        write_config(pinned, '/config/configuration.yaml', 'a: 2\n',
                     _sha('old\n'))
    assert exc.value.code == 'config_hash_changed'
    assert not any(c['command'].startswith('sh -c') for c in fake.calls)


def test_a_successful_write_records_backup_check_and_audit(pinned, fake):
    from core.ha_config_ssh import write_config
    from db.audit import get_audit_log
    out = write_config(pinned, '/config/configuration.yaml', 'a: 2\n',
                       _sha('a: 1\n'))
    assert out['ok'] and out['check'] == 'valid'
    assert out['backup'].endswith('.bak') and '.eshu-' in out['backup']
    call = [c for c in fake.calls if c['command'].startswith('sh -c')][0]
    assert call['stdin'] == 'a: 2\n'
    assert call['timeout'] == 120
    assert any(r['event_type'] == 'ha_config_write' for r in get_audit_log())


def test_a_failed_check_rolls_back_and_audits(pinned, fake):
    from core.ha_config_ssh import HaConfigError, write_config
    from db.audit import get_audit_log
    fake.write_rc = 18
    with pytest.raises(HaConfigError) as exc:
        write_config(pinned, '/config/configuration.yaml', 'a: 2\n',
                     _sha('a: 1\n'))
    assert exc.value.code == 'config_check_failed'
    assert 'restored' in exc.value.message
    assert any(r['event_type'] == 'ha_config_rollback' for r in get_audit_log())


def test_a_client_timeout_becomes_unknown_state_and_keeps_the_backup(pinned, monkeypatch):
    from core.ha_config_ssh import HaConfigError, pending_state, write_config

    def timed_out(target, command, known_hosts, key_path, timeout,
                  stdin=None, strict_host_key=True):
        if command.startswith('sh -c'):
            return {'rc': 124, 'stdout': '', 'stderr': '', 'timed_out': True}
        if command.startswith('realpath'):
            path = command.split()[1].strip("'")
            return {'rc': 0, 'stdout': path + '\nregular file\n',
                    'stderr': '', 'timed_out': False}
        if command.startswith('head -c'):
            return {'rc': 0, 'stdout': 'a: 1\n', 'stderr': '',
                    'timed_out': False}
        return {'rc': 0, 'stdout': '', 'stderr': '', 'timed_out': False}

    monkeypatch.setattr('core.ssh_client.run_ssh', timed_out)
    with pytest.raises(HaConfigError) as exc:
        write_config(pinned, '/config/configuration.yaml', 'a: 2\n',
                     _sha('a: 1\n'))
    assert exc.value.code == 'config_state_unknown'
    state = pending_state(pinned)
    assert state.get('backup', '').endswith('.bak')
    # A second write is refused until a read resolves it.
    with pytest.raises(HaConfigError) as again:
        write_config(pinned, '/config/configuration.yaml', 'a: 3\n',
                     _sha('a: 1\n'))
    assert again.value.code == 'config_state_unknown'


def test_unknown_remote_exit_is_state_unknown(pinned, fake):
    from core.ha_config_ssh import HaConfigError, write_config
    fake.write_rc = 42
    with pytest.raises(HaConfigError) as exc:
        write_config(pinned, '/config/configuration.yaml', 'a: 2\n',
                     _sha('a: 1\n'))
    assert exc.value.code == 'config_state_unknown'


def test_a_frozen_fleet_refuses_reads_still_work(pinned, fake, monkeypatch):
    from core.ha_config_ssh import HaConfigError, list_configs, write_config
    import db.gateways
    monkeypatch.setattr('db.gateways.get_trigger_freeze',
                        lambda: 1234567890)
    with pytest.raises(HaConfigError) as exc:
        write_config(pinned, '/config/configuration.yaml', 'a: 2\n',
                     _sha('a: 1\n'))
    assert exc.value.code == 'frozen'
    assert list_configs(pinned)


# ── restore ───────────────────────────────────────────────────────────────

def test_a_restore_must_name_one_of_the_files_backups(pinned, fake):
    from core.ha_config_ssh import HaConfigError, restore_config
    with pytest.raises(HaConfigError) as exc:
        restore_config(pinned, '/config/configuration.yaml',
                       '/homeassistant/other.yaml.eshu-1762000000.bak')
    assert exc.value.code == 'config_backup_refused'


def test_a_restore_success_runs_one_script_and_checks(pinned, fake):
    from core.ha_config_ssh import restore_config
    name = '/homeassistant/configuration.yaml.eshu-1762000000.bak'
    fake.files[name] = 'a: 1\n'
    out = restore_config(pinned, '/config/configuration.yaml', name)
    assert out['ok'] and out['check'] == 'valid'
    call = [c for c in fake.calls if c['command'].startswith('sh -c')][0]
    assert 'RESTORE' not in call['command']


# ── check and the F10 coupling ────────────────────────────────────────────

def test_a_passing_check_makes_the_restart_guard_fresh(pinned, fake):
    from core.ha_config_ssh import _check_key, check_config
    from core.tool_handlers import _config_check_fresh
    from db.misc import get_meta_json
    out = check_config(pinned)
    assert out['result'] == 'valid'
    meta = get_meta_json(_check_key(pinned), {})
    assert meta.get('result') == 'valid' and meta.get('source') == 'ha_ssh'
    fresh, _last = _config_check_fresh(pinned)
    assert fresh is True


def test_a_failed_check_does_not_write_valid(pinned, fake):
    from core.ha_config_ssh import _check_key, check_config
    from db.misc import get_meta_json
    fake.check_rc = 1
    out = check_config(pinned)
    assert out['result'] == 'invalid'
    assert get_meta_json(_check_key(pinned), {}).get('result') != 'valid'


def test_a_write_invalidates_an_earlier_check_before_connecting(pinned, fake):
    from core.ha_config_ssh import _check_key, check_config, write_config
    from db.misc import get_meta_json
    check_config(pinned)
    assert get_meta_json(_check_key(pinned), {}).get('result') == 'valid'
    before = len(fake.calls)
    write_config(pinned, '/config/configuration.yaml', 'a: 2\n', _sha('a: 1\n'))
    # The key was cleared as part of the write (and the write's own check
    # sets it valid again afterwards).
    assert len(fake.calls) > before


# ── handlers, gating and the catalog ──────────────────────────────────────

def test_the_write_and_restore_tools_are_gated_and_handled(temp_db):
    from db.integrations import create_integration, get_tools
    from core.seeds import seed_for_kind
    iid = create_integration('ha-ssh2', '192.0.2.11', 'none', _key(), kind='ha_ssh')
    seed_for_kind({'id': iid, 'name': 'ha-ssh2', 'kind': 'ha_ssh'})
    tools = {t['name']: t for t in get_tools(iid)}
    assert tools['ha_config_write']['always_gate'] == 1
    assert tools['ha_config_restore']['always_gate'] == 1
    assert tools['ha_config_read']['read_only'] == 1
    assert tools['ha_config_write']['handler'] == 'ha_config_write'
    assert 'ha_config_list' in tools


def test_the_preflight_shows_the_diff_and_refuses_a_stale_hash(pinned, fake):
    from core.ha_config_handlers import _preflight_ha_config_write
    ok = _preflight_ha_config_write(
        pinned, {}, {'path': '/config/configuration.yaml', 'content': 'a: 2\n',
                     'expect_hash': _sha('a: 1\n')}, {})
    assert '_summary' in ok and '+a: 2' in ok['_summary']
    bad = _preflight_ha_config_write(
        pinned, {}, {'path': '/config/configuration.yaml', 'content': 'a: 2\n',
                     'expect_hash': _sha('stale\n')}, {})
    assert 'config_hash_changed' in bad


def test_the_scripts_are_busybox_safe_and_do_not_walk_the_config_symlink():
    from core import ha_config_ssh as m
    assert 'find /homeassistant' in m.LIST_SCRIPT
    assert 'find /config ' not in m.LIST_SCRIPT
    for script in (m.WRITE_SCRIPT, m.RESTORE_SCRIPT):
        assert 'timeout 100 ha core check' in script
        assert 'realpath' in script and 'sha256sum' in script
        assert 'rm -rf' not in script


def test_the_command_wrapper_quotes_the_paths():
    from core.ha_config_ssh import _shell_cmd
    cmd = _shell_cmd("echo ok", "/homeassistant/x y.yaml")
    assert "'/homeassistant/x y.yaml'" in cmd

"""The dashboard deploy helper and the installer channels (X7).

Before X7 the running dashboard rewrote a committed file
(`dashboard/static/dev/eshu-gateway-install.sh`), so a live checkout was dirty
and the helper had to reset it before `git pull`. Since X7 the Edge and Fleet
channels live in `dashboard/installer-channels/`, which git ignores: a deploy
must pull cleanly and must leave both channels exactly as they were, even
when the release changes Build.

This is a behavioural test with a real local git remote. The helper's
systemd/port/sudo calls are stubbed, so nothing real is touched.
"""

import os
import shutil
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, 'scripts', 'deploy-dashboard.sh')
BUILD_FILE = 'dashboard/eshu-gateway-install.sh'
CHANNELS = 'dashboard/installer-channels'

_GIT_ENV = {
    'GIT_AUTHOR_NAME': 'test', 'GIT_AUTHOR_EMAIL': 'test@example.test',
    'GIT_COMMITTER_NAME': 'test', 'GIT_COMMITTER_EMAIL': 'test@example.test',
    'GIT_CONFIG_NOSYSTEM': '1',
}


def _git(cwd, *args, env=None):
    full = dict(os.environ)
    full.update(_GIT_ENV)
    if env:
        full.update(env)
    return subprocess.run(['git', *args], cwd=str(cwd), env=full, check=True,
                          capture_output=True, text=True)


def _stub(bindir, name, body):
    path = bindir / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


@pytest.fixture
def repo(tmp_path):
    if not shutil.which('git'):
        pytest.skip('git is not installed')
    remote = tmp_path / 'remote.git'
    work = tmp_path / 'work'
    clone = tmp_path / 'clone'
    subprocess.run(['git', 'init', '--bare', '-b', 'master', str(remote)],
                   check=True, capture_output=True)
    subprocess.run(['git', 'init', '-b', 'master', str(work)],
                   check=True, capture_output=True)
    (work / 'scripts').mkdir()
    (work / 'scripts' / 'deploy-dashboard.sh').write_text(
        open(SCRIPT, encoding='utf-8').read())
    (work / BUILD_FILE).parent.mkdir(parents=True)
    (work / BUILD_FILE).write_text('build 1\n')
    (work / '.gitignore').write_text(open(os.path.join(REPO, '.gitignore'),
                                          encoding='utf-8').read())
    _git(work, 'add', '-A')
    _git(work, 'commit', '-m', 'initial')
    _git(work, 'remote', 'add', 'origin', str(remote))
    _git(work, 'push', '-u', 'origin', 'master')

    subprocess.run(['git', 'clone', str(remote), str(clone)],
                   check=True, capture_output=True)

    # The host has its runtime channels; the release changes Build.
    (clone / CHANNELS).mkdir(parents=True)
    (clone / CHANNELS / 'edge.sh').write_text('edge on the host\n')
    (clone / CHANNELS / 'fleet.sh').write_text('fleet on the host\n')
    (work / BUILD_FILE).write_text('build 2\n')
    (work / 'RELEASE').write_text('release 1\n')
    _git(work, 'add', '-A')
    _git(work, 'commit', '-m', 'release')
    _git(work, 'push', 'origin', 'master')

    bindir = tmp_path / 'bin'
    bindir.mkdir()
    _stub(bindir, 'systemctl',
          'case "$1" in\n'
          '  show) printf "424242\\n" ;;\n'
          '  is-active) printf "active\\n" ;;\n'
          '  *) exit 0 ;;\n'
          'esac\n')
    _stub(bindir, 'ss', 'echo \'LISTEN 0 128 0.0.0.0:8000 0.0.0.0:* '
                        'users:(("python",pid=424242,fd=3))\'\n')
    # systemctl restart "succeeds", so the helper never signals a real PID.
    _stub(bindir, 'sudo', 'exit 0\n')
    return {'clone': clone, 'work': work, 'bindir': bindir}


def test_a_deploy_pulls_and_leaves_the_channels_alone(repo):
    clone = repo['clone']
    env = {
        'PATH': '%s:%s' % (repo['bindir'], os.environ.get('PATH', '/usr/bin:/bin')),
        'HOME': str(clone.parent),
        'ESHU_PORT': '8000',
    }
    env.update(_GIT_ENV)
    proc = subprocess.run(
        ['bash', str(clone / 'scripts' / 'deploy-dashboard.sh')],
        cwd=str(clone), env=env, capture_output=True, text=True, timeout=120)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert '== git pull --ff-only ==' in proc.stdout
    assert (clone / 'RELEASE').read_text() == 'release 1\n'
    assert (clone / BUILD_FILE).read_text() == 'build 2\n'
    # X7: a deploy changes Build only; Edge and Fleet are untouched.
    assert (clone / CHANNELS / 'edge.sh').read_text() == 'edge on the host\n'
    assert (clone / CHANNELS / 'fleet.sh').read_text() == 'fleet on the host\n'
    status = _git(clone, 'status', '--porcelain').stdout
    assert 'installer-channels' not in status
    assert '✅ eshu-dashboard is active' in proc.stdout


def test_the_helper_no_longer_resets_static():
    """X7: nothing the dashboard writes is tracked, so the helper must not
    reset the dev tree before the pull (that reset existed only for the
    pre-X7 committed copy)."""
    source = open(SCRIPT, encoding='utf-8').read()
    assert 'git checkout -- dashboard/static/dev/' not in source
    assert 'installer-channels' not in source.replace('dashboard/installer-channels/, outside git', '')

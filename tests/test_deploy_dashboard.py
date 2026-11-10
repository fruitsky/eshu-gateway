"""The dashboard deploy helper must not be stopped by the generated dev tree.

RELEASE_1.md precondition 2: the running dashboard rewrites
`dashboard/static/dev/eshu-gateway-install.sh` (its dev-push path), so a live
checkout is dirty and `git pull --ff-only` refuses — the deploy would stop
before restarting anything. The helper now resets that tree *before* the pull.

This is a behavioural test with a real local git remote: the clone's dev file
is dirty, and an upstream commit changes the same file. The helper's
systemd/port/sudo calls are stubbed, so nothing real is touched.
"""

import os
import shutil
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, 'scripts', 'deploy-dashboard.sh')
DEV_FILE = 'dashboard/static/dev/eshu-gateway-install.sh'

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
    (work / DEV_FILE).parent.mkdir(parents=True)
    (work / DEV_FILE).write_text('version 1\n')
    _git(work, 'add', '-A')
    _git(work, 'commit', '-m', 'initial')
    _git(work, 'remote', 'add', 'origin', str(remote))
    _git(work, 'push', '-u', 'origin', 'master')

    subprocess.run(['git', 'clone', str(remote), str(clone)],
                   check=True, capture_output=True)

    # The host rewrote the dev installer, and the release changes it too.
    (clone / DEV_FILE).write_text('version 1 (written on the host)\n')
    (work / DEV_FILE).write_text('version 2\n')
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


def test_the_pull_is_not_blocked_by_the_generated_dev_tree(repo):
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
    # The pull applied the release commit instead of refusing on the dirty file.
    assert (clone / 'RELEASE').read_text() == 'release 1\n'
    assert (clone / DEV_FILE).read_text() == 'version 2\n'
    assert '✅ eshu-dashboard is active' in proc.stdout


def test_the_reset_happens_before_the_pull():
    """A source-level guard: the runtime test above only fails on a dirty clone."""
    source = open(SCRIPT, encoding='utf-8').read()
    reset = source.index('git checkout -- dashboard/static/dev/')
    pull = source.index('\n  git pull --ff-only\n')   # the command, not the echo
    assert reset < pull

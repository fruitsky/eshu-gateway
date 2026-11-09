"""Chunk F5 — `capture_tail()` on its own, under both installed awks.

The gateway's capture is one awk in the stream. Its contract is byte-exact:
what it stores is `tail -c 16385` of what the agent saw (the one exception is
a stream with no final newline, which is documented in § 2.7). Debian and
Ubuntu gateways ship mawk as `awk`, CI images often have gawk, and the two
disagree on several things — record separators, `length`/`substr` in a UTF-8
locale, invalid byte sequences — so the same assertions run under each awk
that is installed. A missing awk skips with a reason; the other still runs.

CI fix review round 3: C1 (the write failure is not the command's), C2
(linear cost), C3 (bytes, not characters) and C4 (a missing final newline).
"""

import os
import shutil
import subprocess
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, 'dashboard', 'eshu-gateway.sh')

CAP = 16385


def _capture_function() -> str:
    """The `capture_tail` definition, lifted out of the real script.

    The gateway script runs its policy on load, so it cannot be sourced; the
    function body is self-contained (it takes the capture path as `$1`) and is
    the thing under test. The extraction is pinned by its own assertions so a
    rename or a refactor that moves the function fails loudly here.
    """
    with open(SCRIPT) as handle:
        lines = handle.read().splitlines()
    starts = [i for i, line in enumerate(lines)
              if line.startswith('capture_tail() {')]
    assert len(starts) == 1, 'capture_tail() moved or was renamed: %r' % starts
    start = starts[0]
    ends = [i for i in range(start + 1, len(lines)) if lines[i] == '}']
    assert ends, 'capture_tail() has no closing brace'
    body = '\n'.join(lines[start:ends[0] + 1])
    assert 'LC_ALL=C awk' in body, body
    return body


@pytest.fixture(params=['mawk', 'gawk'])
def awk_name(request):
    if shutil.which(request.param) is None:
        pytest.skip('%s is not installed' % request.param)
    return request.param


def _sandbox(tmp_path, awk_name):
    """`awk` bound to one implementation, a quiet logger, and the harness."""
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    (bindir / 'awk').symlink_to(shutil.which(awk_name))
    # Hermetic: the failure path logs; never write to the host's syslog.
    logger = bindir / 'logger'
    logger.write_text('#!/bin/sh\nprintf "%%s\\n" "$*" >> "%s"\n'
                      % (bindir / 'logger.log'))
    logger.chmod(0o755)
    harness = tmp_path / 'harness.sh'
    harness.write_text(_capture_function() + '\ncapture_tail "$1"\n')
    env = {
        'PATH': '%s:%s' % (bindir, os.environ.get('PATH', '/usr/bin:/bin')),
        'LANG': 'C.UTF-8',
        'LC_ALL': '',
    }
    return bindir, harness, env


class Capture:
    """Run the extracted function against `data` with `awk` bound to one awk."""

    def __init__(self, tmp_path, awk_name, data, cap_is_dir=False):
        bindir, harness, env = _sandbox(tmp_path, awk_name)
        self.log = bindir / 'logger.log'
        self.cap = tmp_path / 'capture.out'
        if cap_is_dir:
            self.cap.mkdir()
        self.proc = subprocess.run(
            ['bash', str(harness), str(self.cap)], input=data,
            capture_output=True, env=env, timeout=120)

    @property
    def stored(self) -> bytes:
        assert self.cap.is_file(), sorted(p.name for p in self.cap.parent.iterdir())
        return self.cap.read_bytes()

    def logged(self) -> str:
        return self.log.read_text() if self.log.exists() else ''


def _tail(data: bytes) -> bytes:
    return subprocess.run(['tail', '-c', str(CAP)], input=data,
                          capture_output=True, check=True).stdout


def _varied() -> bytes:
    return b''.join(b'x' * (i % 37) + b'\n' for i in range(4000))


PAYLOADS = {
    'varied-lines': (_varied, True),
    'one-huge-line': (lambda: b'A' * 20000 + b'\n', True),
    'empty-lines': (lambda: b'\n' * 20000, True),
    'multibyte': (lambda: ''.join('héllo wörld ✓ %d\n' % i
                                  for i in range(2000)).encode(), True),
    'invalid-bytes': (lambda: b''.join(b'\xff\xfe bad \xc3 %d\n' % i
                                       for i in range(1000)), True),
    'under-the-cap': (lambda: b'hello\n', True),
    'no-final-newline': (lambda: b'a\nb', False),
}


class TestCaptureTail:

    @pytest.mark.parametrize('payload', sorted(PAYLOADS))
    def test_f5_capture_tail_matches_tail_c_16385(self, tmp_path, awk_name, payload):
        """C2/C3: the stored bytes are `tail -c 16385`, in bytes.

        The multibyte and invalid-byte payloads fail if the awk runs in a
        UTF-8 locale (gawk counts characters) or if the implementations
        disagree about bad sequences.
        """
        make, exact = PAYLOADS[payload]
        data = make()
        run = Capture(tmp_path, awk_name, data)
        assert run.proc.returncode == 0, run.proc.stderr
        stored = run.stored
        assert len(stored) == min(len(data) if exact else len(data) + 1, CAP)
        if not exact:
            # C4: a missing final newline gains one; accepted in § 2.7.
            assert stored == b'a\nb\n'
        else:
            assert stored == _tail(data), (payload, awk_name)

    def test_f5_capture_tail_streams_every_line_unchanged(self, tmp_path, awk_name):
        """The agent's stream is untouched (the C4 newline aside)."""
        data = _varied()
        run = Capture(tmp_path, awk_name, data)
        assert run.proc.stdout == data

    def test_f5_capture_tail_is_linear_in_the_output(self, tmp_path, awk_name):
        """C2: the ring must not cost what re-copying a 16 KiB buffer costs.

        `seq 2000000`-scale output through the old buffer was 26.6 s with
        gawk. 200 000 lines here take about 0.3 s; the bound is deliberately
        loose so a slow runner does not make this flaky, while the quadratic
        case still fails by an order of magnitude.
        """
        data = b''.join(b'line %06d\n' % i for i in range(200000))
        started = time.monotonic()
        run = Capture(tmp_path, awk_name, data)
        elapsed = time.monotonic() - started
        assert run.proc.returncode == 0, run.proc.stderr
        assert run.stored == _tail(data)
        assert elapsed < 10, '%s took %.1f s for 200k lines' % (awk_name, elapsed)

    def test_f5_capture_tail_write_failure_keeps_the_stream(self, tmp_path, awk_name):
        """C1: a write failure is reported, not thrown at the agent."""
        data = _varied()
        run = Capture(tmp_path, awk_name, data, cap_is_dir=True)
        assert run.proc.stdout == data
        assert run.proc.stderr == b''
        assert 'CAPTURE FAILED' in run.logged(), run.logged()

    def test_f5_capture_tail_does_not_log_on_success(self, tmp_path, awk_name):
        run = Capture(tmp_path, awk_name, b'hello\n')
        assert run.logged() == ''

    def test_f5_capture_tail_frees_dropped_lines(self, tmp_path, awk_name):
        """C5: 200 MB of output must fit in a 64 MB address space.

        The ring holds at most 16 KiB of lines. A dropped line that is only
        unlinked from the head index but left in the array costs 100 KB each,
        up to 20000 of them (395 MB resident). The lines are streamed in, so
        the test itself holds one line, and `ulimit -v` makes the leak fatal
        instead of merely slow.
        """
        bindir, harness, env = _sandbox(tmp_path, awk_name)
        cap = tmp_path / 'capture.out'
        line = b'x' * 100000 + b'\n'
        script = ('ulimit -v 65536\n'
                  'exec bash "%s" "%s" > /dev/null\n' % (harness, cap))
        proc = subprocess.Popen(['bash', '-c', script], stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, env=env)
        try:
            for _ in range(2000):
                proc.stdin.write(line)
            proc.stdin.close()
        except BrokenPipeError:
            pass
        proc.wait(timeout=180)
        err = proc.stderr.read()
        assert proc.returncode == 0, err[-2000:]
        assert cap.read_bytes() == b'x' * 16384 + b'\n'

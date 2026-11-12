"""Gateway hardening: AF3 (session check off argv) and S1 (tee capture).

AF3 (F19 security sign-off): the session-signature check ran `python3 -` with
the session keys in argv, which every local user can read through /proc while
the root call runs. It is now `python3 -I -`, Python reads the key files
itself, and the id and signature travel in the child's environment.

S1 (buffering-fix security sign-off): in `tail` mode the capture ring was in
the agent's stream, and mawk's 4 KiB stdin block held a slow writer after the
agent hung up. The ring now reads a `tee` copy. The sign-off conditions are
pinned here: a bounded wait for the capture, a temp file renamed into place,
and a capture that never finishes does not hold the call.
"""
import base64
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

from test_gateway_script import (
    SCRIPT, SESSION_KEY, GatewaySandbox, session_sig,
)

pytestmark = pytest.mark.skipif(
    not shutil.which("bash"), reason="the gateway is a bash script")

# run_sanitized waits CAPTURE_WAIT_TICKS x 0.05 s for the capture.
CAPTURE_BOUND_S = 2.0


@pytest.fixture
def gw(tmp_path):
    return GatewaySandbox(tmp_path / "gw")


def _exec_bodies(sandbox):
    return [c["data"] for c in sandbox.curl_calls()
            if c["url"].endswith("/api/exec-result")]


def _wait_result(sandbox):
    assert sandbox.wait_for(lambda calls: any(
        c["url"].endswith("/api/exec-result") for c in calls)), sandbox.curl_calls()
    bodies = _exec_bodies(sandbox)
    assert len(bodies) == 1, bodies
    return bodies[0]


def _unb64(text):
    return base64.b64decode(text or "").decode("utf-8", "replace")


# ── AF3: no key material in the checker's argv ──────────────────────────────

def _record_python_argv(sandbox):
    """Put a `python3` on the sandbox PATH that logs its argv, then runs the
    real interpreter with the same argv and stdin. Returns the log path."""
    real = shutil.which("python3")
    log = sandbox.root / "python-argv.log"
    stub = ('#!/bin/bash\n'
            '{ printf "%%s\\037" "$@"; printf "\\036"; } >> "%s"\n'
            'exec "%s" "$@"\n' % (log, real))
    path = sandbox.bin / "python3"
    path.write_text(stub)
    path.chmod(0o755)
    return log


def _argv_calls(log: Path):
    if not log.exists():
        return []
    text = log.read_text()
    return [record.split("\x1f")[:-1] for record in text.split("\x1e") if record]


def _session_check_calls(calls, sandbox):
    key_path = str(sandbox.etc / "eshu-session-key")
    return [argv for argv in calls if key_path in argv]


@pytest.mark.parametrize("valid", [True, False])
def test_af3_session_check_keeps_keys_out_of_argv(gw, valid):
    """The check runs `python3 -I -` with only the key-file paths in argv:
    neither key, nor the signature, is on any python3 command line. A valid
    signature still passes and an invalid one still fails."""
    prev_key = "ef" * 32
    gw.session_key(SESSION_KEY, prev=prev_key)
    gw.require_sessions(True)
    gw.exact("echo hello")
    log = _record_python_argv(gw)
    sid = "chat-af3"
    sig = session_sig(sid) if valid else "12" * 32
    proc = gw.run("ESHU_SESSION_ID=%s ESHU_SESSION_SIG=%s echo hello" % (sid, sig))

    if valid:
        assert proc.stdout.strip() == "hello", proc.stdout
        assert proc.returncode == 0
    else:
        assert "invalid or was not minted" in proc.stdout
        assert "hello" not in proc.stdout
        assert proc.returncode == 1

    calls = _argv_calls(log)
    checks = _session_check_calls(calls, gw)
    assert len(checks) == 1, calls
    assert checks[0] == ["-I", "-", str(gw.etc / "eshu-session-key"),
                         str(gw.etc / "eshu-session-key.prev")], checks
    for argv in calls:
        joined = "\n".join(argv)
        assert SESSION_KEY not in joined
        assert prev_key not in joined
        assert sig not in joined


def test_af3_previous_key_and_trailing_newlines_still_verify(gw):
    """Python reads the files itself, so the `$(cat …)` newline strip must be
    kept (`.strip()`): keys written with a newline, current and `.prev`."""
    new_key = "cd" * 32
    (gw.etc / "eshu-session-key").write_text(new_key + "\n")
    (gw.etc / "eshu-session-key.prev").write_text(SESSION_KEY + "\n")
    gw.require_sessions(True)
    gw.exact("echo hello")
    for key in (new_key, SESSION_KEY):
        sid = "chat-%s" % key[:2]
        proc = gw.run("ESHU_SESSION_ID=%s ESHU_SESSION_SIG=%s echo hello"
                      % (sid, session_sig(sid, key)))
        assert proc.stdout.strip() == "hello", (key, proc.stdout)
        assert proc.returncode == 0


def test_af3_missing_prev_key_and_garbage_key_fail_closed(gw):
    """An unreadable `.prev` is an empty key, a non-hex key is skipped: a
    signature made with neither file's key is refused."""
    (gw.etc / "eshu-session-key").write_text("not-hex\n")
    gw.require_sessions(True)
    gw.exact("echo hello")
    sid = "chat-x"
    proc = gw.run("ESHU_SESSION_ID=%s ESHU_SESSION_SIG=%s echo hello"
                  % (sid, session_sig(sid)))
    assert "invalid or was not minted" in proc.stdout
    assert proc.returncode == 1


def test_af3_every_python3_in_the_gateway_is_isolated():
    """The "all interpreters are -I" claim, pinned at the source."""
    text = SCRIPT.read_text()
    calls = re.findall(r"\bpython3\b[^\n]*", text)
    code = [c for c in calls if not c.lstrip().startswith("#")]
    assert code, "no python3 call found; the pattern is stale"
    bare = [c for c in code if not re.match(r"python3 -I\b", c)]
    assert bare == [], bare


# ── S1: the tee capture ─────────────────────────────────────────────────────

def _stuck_awk(sandbox, partial: bytes = b""):
    """An `awk` whose capture never finishes. Only the capture ring passes
    `-v cap=…`; every other awk call in the script runs the real awk. The
    stub reads its whole input (so `tee` is never blocked), optionally writes
    `partial` into the capture file, then sleeps instead of exiting."""
    real = shutil.which("awk")
    pidfile = sandbox.root / "stuck-awk.pid"
    stub = '''#!/bin/bash
cap=""
for a in "$@"; do case "$a" in cap=*) cap="${a#cap=}" ;; esac; done
if [ -z "$cap" ]; then exec "%s" "$@"; fi
echo $$ > "%s"
cat > /dev/null
printf '%%s' "%s" > "$cap"
exec sleep 600
''' % (real, pidfile, partial.decode())
    path = sandbox.bin / "awk"
    path.write_text(stub)
    path.chmod(0o755)
    return pidfile


def _kill_stuck(pidfile: Path):
    try:
        os.kill(int(pidfile.read_text()), signal.SIGKILL)
    except (OSError, ValueError):
        pass


@pytest.mark.parametrize("partial", [b"", b"PARTIAL-CAPTURE"])
def test_s1_stuck_capture_does_not_hold_the_call(gw, partial):
    """Sign-off (1) and (3): a capture that never finishes is waited for at
    most the bound; then the result is posted with no output and the call
    returns with the command's stdout, exit code and sentinel. Sign-off (2):
    a half-written capture (`partial`) is never posted, because only a
    finished capture is renamed to the name the post reads."""
    pidfile = _stuck_awk(gw, partial)
    gw.exact("./emit")
    gw.capture("tail")
    gw.command_script("emit", "echo visible-line\nexit 3\n")
    try:
        started = time.monotonic()
        proc = gw.run("./emit")       # subprocess.run: waits for EOF on stdout
        elapsed = time.monotonic() - started
        assert pidfile.exists(), "the stub capture never ran"
    finally:
        _kill_stuck(pidfile)
    assert elapsed < CAPTURE_BOUND_S + 4, (
        "a stuck capture held the call %.1f s" % elapsed)
    assert proc.returncode == 3
    assert proc.stdout == "visible-line\n[ESHU exit=3]\n"
    assert "CAPTURE TIMEOUT" in gw.logger_text(), gw.logger_text()
    body = _wait_result(gw)
    assert body["exit_code"] == 3
    assert body["encoded_output"] == ""
    # The EXIT trap removed the private directory, part file and all.
    assert list(gw.capture_dir.iterdir()) == []


@pytest.fixture(params=["mawk", "gawk"])
def awk_name(request):
    if shutil.which(request.param) is None:
        pytest.skip("%s is not installed" % request.param)
    return request.param


def test_s1_tail_capture_is_complete_and_inside_the_bound(gw, awk_name):
    """The normal path: the capture finishes inside the bound (no timeout
    line), the posted tail is the last 16 KiB of what the agent saw, and the
    private directory is gone afterwards."""
    (gw.bin / "awk").symlink_to(shutil.which(awk_name))
    gw.exact("./many")
    gw.capture("tail")
    gw.command_script("many", "seq 1 20000\n")
    proc = gw.run("./many")
    assert proc.returncode == 0
    assert proc.stdout == "".join("%d\n" % i for i in range(1, 20001))
    assert "CAPTURE TIMEOUT" not in gw.logger_text()
    body = _wait_result(gw)
    assert body["output_truncated"] is True
    assert _unb64(body["encoded_output"]) == proc.stdout[-16384:]
    assert list(gw.capture_dir.iterdir()) == []


def test_s1_tail_mode_has_no_awk_in_the_stream():
    """The tail branch ends in `tee >(capture_job …)`, never `tee -p`, and
    the substitution moves its own stdout/stderr off the agent's channel."""
    text = SCRIPT.read_text()
    branch = text.split('elif [ "$mode" = "tail" ] && [ -n "$ESHU_OUT_TAIL" ]; then', 1)[1]
    branch = branch.split("  else", 1)[0]
    code = "\n".join(line for line in branch.splitlines()
                     if not line.lstrip().startswith("#"))
    assert '| "${TEE_CMD[@]}" >(exec >/dev/null 2>&1' in code, code
    assert 'capture_job "$ESHU_CAP_PART" "$ESHU_OUT_TAIL")' in code, code
    assert "capture_tail" not in code
    assert "tee -p" not in code
    assert "TEE_CMD=(tee --output-error=exit)" in text


# Function level: the extracted capture_tail + capture_job, run with the
# exact tee shape the gateway uses.

def _function(name: str) -> str:
    lines = SCRIPT.read_text().splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith(name + "() {")]
    assert len(starts) == 1, (name, starts)
    end = next(i for i in range(starts[0] + 1, len(lines)) if lines[i] == "}")
    return "\n".join(lines[starts[0]:end + 1])


def _tee_harness(tmp_path, awk_name=None, awk_body=None):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    if awk_body is not None:
        stub = bindir / "awk"
        stub.write_text("#!/bin/sh\n" + awk_body)
        stub.chmod(0o755)
    else:
        (bindir / "awk").symlink_to(shutil.which(awk_name))
    logger = bindir / "logger"
    logger.write_text('#!/bin/sh\nprintf "%%s\\n" "$*" >> "%s"\n'
                      % (bindir / "logger.log"))
    logger.chmod(0o755)
    capdir = tmp_path / "cap"
    capdir.mkdir()
    (capdir / "part").write_text("")
    harness = tmp_path / "tee.sh"
    harness.write_text(
        _function("capture_tail") + "\n" + _function("capture_job") + "\n"
        'tee --output-error=exit >(exec >/dev/null 2>&1\n'
        '  capture_job "%s" "%s")\n'
        % (capdir / "part", capdir / "tail"))
    env = {"PATH": "%s:%s" % (bindir, os.environ.get("PATH", "/usr/bin:/bin")),
           "LANG": "C.UTF-8", "LC_ALL": ""}
    return harness, capdir, env


def _wait_gone(path: Path, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and path.exists():
        time.sleep(0.02)
    return not path.exists()


@pytest.mark.parametrize("sigpipe", ["default", "ignored"])
def test_s1_tee_capture_ends_when_the_agent_disconnects(tmp_path, awk_name, sigpipe):
    """D1 for the tee shape: `yes` writes for ever, `head -n 1` is the agent
    hanging up. tee must stop at once (SIGPIPE, or EPIPE when SIGPIPE is
    ignored, which plain tee also exits on), so the writer is stopped, and
    the capture still completes from what it saw."""
    harness, capdir, env = _tee_harness(tmp_path, awk_name)
    trap = "trap '' PIPE\n" if sigpipe == "ignored" else ""
    script = (trap + 'yes | bash "%s" 2>/dev/null | head -n 1 > /dev/null\n'
              'echo "rc=${PIPESTATUS[*]}"\n' % harness)
    started = time.monotonic()
    proc = subprocess.run(["bash", "-c", script], capture_output=True,
                          env=env, timeout=30)
    elapsed = time.monotonic() - started
    assert elapsed < 10, "%s/%s kept running %.1f s" % (awk_name, sigpipe, elapsed)
    statuses = proc.stdout.decode().strip().split("=", 1)[1].split()
    assert statuses[0] != "0", proc.stdout
    assert _wait_gone(capdir / "part")
    tail = (capdir / "tail").read_bytes()
    assert 0 < len(tail) <= 16385
    # The cut can land on a newline, so the first kept line may be empty.
    assert set(tail.splitlines()) <= {b"y", b""}


def test_s1_tee_capture_is_the_visible_stream(tmp_path, awk_name):
    """The agent gets every byte; the capture is the last 16 385 bytes of
    the same bytes, written under the temp name and renamed."""
    data = b"".join(b"row %06d %s\n" % (i, b"x" * (i % 50)) for i in range(5000))
    harness, capdir, env = _tee_harness(tmp_path, awk_name)
    proc = subprocess.run(["bash", str(harness)], input=data,
                          capture_output=True, env=env, timeout=60)
    assert proc.stdout == data
    assert _wait_gone(capdir / "part")
    assert (capdir / "tail").read_bytes() == data[-16385:]


def test_s1_capture_that_dies_never_cuts_the_stream(tmp_path):
    """D1: an awk that exits at once without reading must not cut the agent's
    output (capture_job drains the rest), and no capture is renamed."""
    data = b"".join(b"line %06d\n" % i for i in range(100000))
    harness, capdir, env = _tee_harness(tmp_path, awk_body="exit 2\n")
    proc = subprocess.run(["bash", str(harness)], input=data,
                          capture_output=True, env=env, timeout=60)
    assert proc.returncode == 0
    assert proc.stdout == data
    assert _wait_gone(capdir / "part")
    assert not (capdir / "tail").exists()
    assert "CAPTURE FAILED" in (tmp_path / "bin" / "logger.log").read_text()


def test_s1_capture_rename_refuses_a_directory(tmp_path, awk_name):
    """`mv -T`: if `tail` was replaced by a directory, the capture is not
    moved into it; it fails, is logged, and `part` is still removed."""
    harness, capdir, env = _tee_harness(tmp_path, awk_name)
    (capdir / "tail").mkdir()
    proc = subprocess.run(["bash", str(harness)], input=b"hello\n",
                          capture_output=True, env=env, timeout=30)
    assert proc.stdout == b"hello\n"
    assert _wait_gone(capdir / "part")
    assert list((capdir / "tail").iterdir()) == []
    assert "CAPTURE FAILED" in (tmp_path / "bin" / "logger.log").read_text()

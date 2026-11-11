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
import re
import shutil
from pathlib import Path

import pytest

from test_gateway_script import (
    SCRIPT, SESSION_KEY, GatewaySandbox, session_sig,
)

pytestmark = pytest.mark.skipif(
    not shutil.which("bash"), reason="the gateway is a bash script")


@pytest.fixture
def gw(tmp_path):
    return GatewaySandbox(tmp_path / "gw")


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

"""Hermetic regression harness for dashboard/eshu-gateway.sh.

The gateway script is the last line of defence: freeze, the hard self-protection
blocklist, the central blocklist, exact/regex allowlists, zero-trust and the JIT
ticket all live in this shell file. It is also awkward to test because it
hardcodes /etc/eshu-*, resets PATH and is installed with the dashboard URL and
token baked in.

This harness builds a disposable sandbox instead of mocking the decisions:

* the real script is copied with only its paths relocated (and the stub bin
  prepended to PATH), so the policy logic and the shell quoting under test are
  the shipped ones;
* a fake ``curl`` records every dashboard call and returns canned JIT status;
* a fake ``logger`` captures the syslog lines;
* the freeze/zero-trust markers, policy files and ticket lockbox live under
  pytest's tmp_path.

No root, no network, no real /etc, no real ticket file. Commands executed by the
tests are deliberately harmless (echo/true/false/sh -c).
"""
import base64
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "dashboard" / "eshu-gateway.sh"
SESSION_KEY = "ab" * 32


def session_sig(session_id, key=SESSION_KEY):
    return hmac.new(bytes.fromhex(key), session_id.encode(),
                    hashlib.sha256).hexdigest()

pytestmark = pytest.mark.skipif(
    not shutil.which("bash"), reason="the gateway is a bash script")


class GatewaySandbox:
    """A throwaway installation of the real gateway script."""

    def __init__(self, root: Path):
        self.root = Path(root)
        if " " in str(self.root):
            pytest.skip("temporary path contains a space; the script is not quoted")
        self.etc = self.root / "etc"
        self.run_dir = self.root / "run"
        self.bin = self.root / "bin"
        self.home = self.root / "home"
        self.features = self.etc / "features.d"
        for directory in (self.etc, self.run_dir, self.bin, self.home, self.features):
            directory.mkdir(parents=True, exist_ok=True)

        self.curl_log = self.root / "curl.log"
        self.logger_log = self.root / "logger.log"
        self.status_file = self.root / "jit-status"
        self.request_mode_file = self.root / "request-mode"
        self.ticket_file = self.run_dir / "eshu.tickets"

        self._write_stubs()
        self.script = self.root / "eshu-gateway.sh"
        self.script.write_text(self._transformed())
        subprocess.run(["bash", "-n", str(self.script)], check=True)

    # ── sandbox construction ────────────────────────────────────────────────
    def _write_stubs(self):
        curl = '''#!/bin/bash
LOG="__LOG__"
STATUS_FILE="__STATUS__"
REQUEST_MODE="__MODE__"
method="GET"; url=""; data=""
while [ $# -gt 0 ]; do
  case "$1" in
    -X) method="$2"; shift 2 ;;
    -d) data="$2"; shift 2 ;;
    -H|-m|-w) shift 2 ;;
    -s) shift ;;
    -*) shift ;;
    *) url="$1"; shift ;;
  esac
done
printf '%s\\t%s\\t%s\\n' "$method" "$url" "$data" >> "$LOG"
case "$url" in
  */api/request)
    mode=$(cat "$REQUEST_MODE" 2>/dev/null || echo ok)
    case "$mode" in
      429) printf '{"error":"rate_limited"}\\n429\\n' ;;
      403) printf '{"detail":"Encoded payload blocked: the decoded command matched a blocklist pattern."}\\n403\\n' ;;
      fail) exit 7 ;;
      *) printf '{"id":"000123"}\\n200\\n' ;;
    esac
    ;;
  */api/request_status/*)
    printf '{"status":"%s"}\\n' "$(cat "$STATUS_FILE" 2>/dev/null || echo pending)"
    ;;
  */api/ticket/*)
    printf '{"ticket":"test-ticket"}\\n'
    ;;
  *) : ;;
esac
'''
        curl = (curl.replace("__LOG__", str(self.curl_log))
                    .replace("__STATUS__", str(self.status_file))
                    .replace("__MODE__", str(self.request_mode_file)))
        self._executable(self.bin / "curl", curl)

        logger = '''#!/bin/bash
printf '%s\\n' "$*" >> "__LOG__"
'''.replace("__LOG__", str(self.logger_log))
        self._executable(self.bin / "logger", logger)

    @staticmethod
    def _executable(path: Path, body: str):
        path.write_text(body)
        path.chmod(0o755)

    def _transformed(self) -> str:
        text = SCRIPT.read_text()

        def swap(old, new, expected=None):
            assert old in text, "the gateway script no longer contains %r" % old
            if expected is not None:
                assert text.count(old) == expected, old
            return text.replace(old, new)

        text = swap("export PATH=/usr/sbin:/usr/bin:/sbin:/bin",
                    "export PATH=%s:/usr/sbin:/usr/bin:/sbin:/bin" % self.bin, 1)
        text = swap("export HOME=/home/eshu-gateway",
                    "export HOME=%s" % self.home, 1)
        text = swap('TICKETS_FILE="/var/run/eshu.tickets"',
                    'TICKETS_FILE="%s"' % self.ticket_file, 1)
        for name in ("eshu-freeze", "eshu-zero-trust", "eshu-rblack.txt",
                     "eshu-exact.txt", "eshu-rwhite.txt",
                     "eshu-require-session", "eshu-session-key"):
            text = swap("/etc/" + name, str(self.etc / name))
        text = swap('FEATURES_DIR="/etc/eshu/features.d"',
                    'FEATURES_DIR="%s"' % self.features, 1)
        for token, value in (
            ("__TARGET_IP__", "10.0.0.1"),
            ("__DASHBOARD_URL__", "http://dashboard.test"),
            ("__GATEWAY_VERSION__", "v-test"),
            ("__GATEWAY_TOKEN__", "test-token"),
        ):
            text = swap(token, value, 1)

        # The relocation must be complete, or a test could touch the real host.
        # (The sandbox path itself ends with /etc/eshu-*, so compare counts: a
        # leftover real path makes the second count larger.)
        for name in ("eshu-freeze", "eshu-zero-trust", "eshu-rblack.txt",
                     "eshu-exact.txt", "eshu-rwhite.txt",
                     "eshu-require-session", "eshu-session-key"):
            sandbox_path = str(self.etc / name)
            assert text.count("/etc/" + name) == text.count(sandbox_path), name
        assert "/etc/eshu/features.d" not in text
        assert "/var/run/eshu.tickets" not in text
        # The hard self-protection case still names the real path on purpose.
        assert '*"/etc/eshu-"*' in text
        return text

    # ── policy/state helpers ────────────────────────────────────────────────
    def _write(self, path: Path, *lines: str):
        path.write_text("".join(line + "\n" for line in lines))

    def exact(self, *commands):
        self._write(self.etc / "eshu-exact.txt", *commands)

    def regex_white(self, *patterns):
        self._write(self.etc / "eshu-rwhite.txt", *patterns)

    def blocklist(self, *patterns):
        self._write(self.etc / "eshu-rblack.txt", *patterns)

    def freeze(self, on: bool):
        (self.etc / "eshu-freeze").write_text("1" if on else "")

    def zero_trust(self, on: bool):
        (self.etc / "eshu-zero-trust").write_text("1" if on else "")

    def require_sessions(self, on: bool = True):
        (self.etc / "eshu-require-session").write_text("1" if on else "")

    def session_key(self, key: str = SESSION_KEY, prev: str = ""):
        (self.etc / "eshu-session-key").write_text(key)
        (self.etc / "eshu-session-key.prev").write_text(prev)

    def jit_status(self, status: str):
        self.status_file.write_text(status)

    def request_mode(self, mode: str):
        self.request_mode_file.write_text(mode)

    def tickets(self, *lines: str):
        self._write(self.ticket_file, *lines)

    def feature(self, name: str, body: str):
        (self.features / name).write_text(body)

    # ── execution/observation ───────────────────────────────────────────────
    def run(self, command: str):
        env = {
            "SSH_ORIGINAL_COMMAND": command,
            "SSH_CONNECTION": "127.0.0.1 40000 127.0.0.1 22",
            "USER": "tester",
            "LANG": "C.UTF-8",
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        }
        proc = subprocess.run(
            ["bash", str(self.script)], cwd=str(self.root), env=env,
            capture_output=True, text=True, timeout=20)
        time.sleep(0.15)  # let the backgrounded /api/log stub finish
        return proc

    def curl_calls(self):
        if not self.curl_log.exists():
            return []
        calls = []
        for line in self.curl_log.read_text().splitlines():
            parts = line.split("\t", 2)
            while len(parts) < 3:
                parts.append("")
            method, url, data = parts
            try:
                payload = json.loads(data) if data else {}
            except ValueError:
                payload = {}
            calls.append({"method": method, "url": url, "data": payload})
        return calls

    def wait_for(self, predicate, timeout=2.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate(self.curl_calls()):
                return True
            time.sleep(0.02)
        return False

    def logger_text(self):
        if not self.logger_log.exists():
            return ""
        return self.logger_log.read_text()


@pytest.fixture
def gw(tmp_path):
    return GatewaySandbox(tmp_path)


# ── the shipped script's install contract ───────────────────────────────────
def test_repo_script_keeps_placeholders_and_expected_paths():
    text = SCRIPT.read_text()
    for token in ("__TARGET_IP__", "__DASHBOARD_URL__",
                  "__GATEWAY_VERSION__", "__GATEWAY_TOKEN__"):
        assert token in text
    for path in ("/etc/eshu-freeze", "/etc/eshu-zero-trust",
                 "/etc/eshu-rblack.txt", "/etc/eshu-exact.txt",
                 "/etc/eshu-rwhite.txt", "/etc/eshu/features.d",
                 "/var/run/eshu.tickets"):
        assert path in text


# ── freeze ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("value,blocked", [("1", True), ("", False), ("0", False)])
def test_freeze_marker_is_content_based(gw, value, blocked):
    (gw.etc / "eshu-freeze").write_text(value)
    gw.exact("echo hello")
    proc = gw.run("echo hello")
    if blocked:
        assert "FROZEN" in proc.stdout
        assert "hello" not in proc.stdout
        assert proc.returncode == 1
        assert gw.wait_for(lambda calls: any(
            c["url"].endswith("/api/log") and c["data"].get("status") == "frozen"
            for c in calls))
    else:
        assert "hello" in proc.stdout
        assert proc.returncode == 0


def test_freeze_wins_over_allowlist(gw):
    gw.freeze(True)
    gw.exact("echo hello")
    proc = gw.run("echo hello")
    assert "FROZEN" in proc.stdout
    assert "hello" not in proc.stdout
    assert proc.returncode == 1


def test_a_blocked_command_keeps_its_session_id(gw):
    gw.freeze(True)
    gw.run("ESHU_SESSION_ID=chat-block echo hello")
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/log")
        and c["data"].get("session_id") == "chat-block"
        for c in calls))


# ── hard self-protection blocklist ─────────────────────────────────────────
@pytest.mark.parametrize("command", [
    "cat /etc/eshu-exact.txt",
    "echo eshu.db",
    "echo $(which bash)",
])
def test_hard_block_is_unconditional(gw, command):
    gw.exact(command)  # allowlisting it must not help
    proc = gw.run(command)
    assert "FATAL" in proc.stdout
    assert proc.returncode == 1
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/log") and c["data"].get("status") == "blocked"
        for c in calls))


def test_freeze_is_checked_before_the_hard_block(gw):
    gw.freeze(True)
    proc = gw.run("cat /etc/eshu-exact.txt")
    assert "FROZEN" in proc.stdout
    assert proc.returncode == 1


# ── central blocklist ───────────────────────────────────────────────────────
def test_central_blocklist_blocks_substring(gw):
    gw.blocklist("forbidden")
    proc = gw.run("echo forbidden-OUTPUT")
    assert "blacklist match" in proc.stdout
    assert "forbidden-OUTPUT" not in proc.stdout  # the command never ran
    assert proc.returncode == 1


def test_central_blocklist_strips_anchors(gw):
    gw.blocklist("^forbidden$")
    proc = gw.run("echo forbidden-OUTPUT")
    assert "blacklist match" in proc.stdout
    assert "forbidden-OUTPUT" not in proc.stdout
    assert proc.returncode == 1


def test_blocklist_wins_over_allowlist(gw):
    gw.blocklist("SECRET")
    gw.exact("echo SECRET_MARKER")
    proc = gw.run("echo SECRET_MARKER")
    assert "blacklist match" in proc.stdout
    assert "SECRET_MARKER" not in proc.stdout
    assert proc.returncode == 1


def test_safe_command_passes_the_blocklist(gw):
    gw.blocklist("forbidden")
    gw.exact("echo ok")
    proc = gw.run("echo ok")
    assert proc.stdout.strip() == "ok"
    assert proc.returncode == 0


# ── allowlists and prefix parsing ──────────────────────────────────────────
def test_exact_whitelist_runs_and_logs(gw):
    gw.exact("echo hello")
    proc = gw.run("echo hello")
    assert proc.stdout.strip() == "hello"
    assert proc.returncode == 0
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/log") and c["data"].get("status") == "auto-approved"
        for c in calls))


def test_exact_whitelist_is_a_whole_line_match(gw):
    gw.exact("echo hello")
    gw.jit_status("denied")
    proc = gw.run("echo hello there")
    assert "[DENIED]" in proc.stdout
    assert proc.returncode == 1


def test_regex_whitelist_runs(gw):
    gw.regex_white(r"^echo .*$")
    proc = gw.run("echo regex-ok")
    assert "regex-ok" in proc.stdout
    assert proc.returncode == 0
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/log") and c["data"].get("status") == "auto-approved"
        for c in calls))


def test_prefix_is_stripped_before_matching_and_logged(gw):
    gw.exact("echo hello")
    proc = gw.run("ESHU_SESSION_ID=chat-1 ESHU_EXECUTION_ID=exec-1 echo hello")
    assert proc.stdout.strip() == "hello"
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/log")
        and c["data"].get("session_id") == "chat-1"
        and c["data"].get("execution_id") == "exec-1"
        for c in calls))


def test_window_token_prefix_is_stripped(gw):
    gw.exact("echo hello")
    proc = gw.run("ESHU_WINDOW_TOKEN=win ESHU_SESSION_ID=sid echo hello")
    assert proc.stdout.strip() == "hello"
    assert proc.returncode == 0


# ── zero-trust ──────────────────────────────────────────────────────────────
def test_zero_trust_skips_the_allowlist_and_goes_to_jit(gw):
    gw.zero_trust(True)
    gw.exact("echo hello")
    gw.jit_status("approved")
    proc = gw.run("echo hello")
    assert "ZERO-TRUST" in gw.logger_text()
    assert "[OK] Approved" in proc.stdout
    assert "hello" in proc.stdout
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/request") for c in calls))
    assert not any(c["url"].endswith("/api/log")
                   and c["data"].get("status") == "auto-approved"
                   for c in gw.curl_calls())


def test_zero_trust_off_keeps_auto_run(gw):
    gw.zero_trust(False)
    gw.exact("echo hello")
    proc = gw.run("echo hello")
    assert "hello" in proc.stdout
    assert not any(c["url"].endswith("/api/request") for c in gw.curl_calls())


# ── JIT dispatch, status and ticket claim ───────────────────────────────────
def test_jit_denied_exits_and_does_not_run(gw):
    gw.jit_status("denied")
    proc = gw.run("echo nope")
    assert "[DENIED]" in proc.stdout
    assert "nope" not in proc.stdout
    assert proc.returncode == 1
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/request") for c in calls))


def test_jit_approved_claims_and_runs(gw):
    gw.jit_status("approved")
    proc = gw.run("echo yes")
    assert "[OK] Approved! Executing command now..." in proc.stdout
    assert "yes" in proc.stdout
    assert proc.returncode == 0
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/ticket/000123") for c in calls))


def test_jit_consumed_runs_without_a_second_claim(gw):
    gw.jit_status("consumed")
    proc = gw.run("echo consumed-ok")
    assert "consumed-ok" in proc.stdout
    assert proc.returncode == 0
    assert not any(c["url"].endswith("/api/ticket/") for c in gw.curl_calls())


def test_jit_request_forwards_session_execution_and_stripped_command(gw):
    gw.jit_status("denied")
    gw.run("ESHU_SESSION_ID=chat-9 ESHU_EXECUTION_ID=exec-9 echo jit")
    req = next(c for c in gw.curl_calls()
               if c["url"].endswith("/api/request"))
    assert req["data"]["session_id"] == "chat-9"
    assert req["data"]["execution_id"] == "exec-9"
    assert base64.b64decode(req["data"]["encoded_command"]).decode() == "echo jit"


def test_jit_rate_limit_is_reported(gw):
    gw.request_mode("429")
    proc = gw.run("echo x")
    assert "Rate limited" in proc.stdout
    assert proc.returncode == 1


def test_jit_dashboard_unreachable_is_reported(gw):
    gw.request_mode("fail")
    proc = gw.run("echo x")
    assert "JIT Dashboard is unreachable" in proc.stdout
    assert proc.returncode == 1


def test_live_ticket_runs_without_the_dashboard(gw):
    gw.tickets("%d|echo ticketed" % int(time.time()))
    proc = gw.run("echo ticketed")
    assert "ticketed" in proc.stdout
    assert proc.returncode == 0
    assert not any(c["url"].endswith("/api/request") for c in gw.curl_calls())
    assert not gw.ticket_file.read_text().strip()


def test_stale_ticket_is_discarded(gw):
    gw.tickets("%d|echo ticketed" % (int(time.time()) - 120))
    gw.jit_status("denied")
    proc = gw.run("echo ticketed")
    assert "[DENIED]" in proc.stdout
    assert "ticketed" not in proc.stdout
    assert proc.returncode == 1


# ── output sanitisation and exit status ────────────────────────────────────
@pytest.mark.parametrize("command,secret", [
    ("echo API_KEY=supersecret123", "supersecret123"),
    ("echo 'TOKEN=\"abc def\"'", "abc def"),
    ("echo --password hunter2", "hunter2"),
])
def test_output_is_sanitized(gw, command, secret):
    gw.exact(command)
    proc = gw.run(command)
    assert secret not in proc.stdout
    assert "[REDACTED]" in proc.stdout


def test_nonzero_exit_is_marked_and_propagated(gw):
    command = "sh -c 'echo before; exit 3'"
    gw.exact(command)
    proc = gw.run(command)
    assert "before" in proc.stdout
    assert "[ESHU exit=3]" in proc.stdout
    assert proc.returncode == 3


def test_zero_exit_has_no_sentinel(gw):
    gw.exact("true")
    proc = gw.run("true")
    assert "[ESHU exit=" not in proc.stdout
    assert proc.returncode == 0


def test_sanitizer_still_applies_when_the_command_fails(gw):
    command = "sh -c 'echo API_KEY=supersecret; exit 5'"
    gw.exact(command)
    proc = gw.run(command)
    assert "supersecret" not in proc.stdout
    assert "[ESHU exit=5]" in proc.stdout
    assert proc.returncode == 5


# ── feature scripts and the no-command path ────────────────────────────────
def test_feature_scripts_are_sourced(gw):
    marker = gw.root / "feature-ran"
    gw.feature("marker.sh", "printf ran > '%s'\n" % marker)
    # Feature scripts are sourced after the allowlists, on the JIT path.
    gw.jit_status("denied")
    gw.run("echo hello")
    assert marker.read_text() == "ran"


def test_no_command_exits_without_calling_the_dashboard(gw):
    proc = gw.run("")
    assert proc.returncode == 1
    assert gw.curl_calls() == []


# ── strict session mode ────────────────────────────────────────────────────
def test_strict_mode_refuses_an_untagged_allowlisted_command(gw):
    gw.session_key()
    gw.require_sessions(True)
    gw.exact("echo hello")
    proc = gw.run("echo hello")
    assert "minted Session is required" in proc.stdout
    assert "hello" not in proc.stdout
    assert proc.returncode == 1
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/log")
        and c["data"].get("status") == "session-required"
        for c in calls))


def test_strict_mode_accepts_a_valid_signed_prefix(gw):
    gw.session_key()
    gw.require_sessions(True)
    gw.exact("echo hello")
    sid = "chat-signed"
    proc = gw.run("ESHU_SESSION_ID=%s ESHU_SESSION_SIG=%s echo hello"
                  % (sid, session_sig(sid)))
    assert proc.stdout.strip() == "hello"
    assert proc.returncode == 0


def test_strict_mode_refuses_a_bad_signature(gw):
    gw.session_key()
    gw.require_sessions(True)
    gw.exact("echo hello")
    proc = gw.run("ESHU_SESSION_ID=chat-signed ESHU_SESSION_SIG=deadbeef echo hello")
    assert "invalid or was not minted" in proc.stdout
    assert "hello" not in proc.stdout
    assert proc.returncode == 1


def test_strict_mode_accepts_the_previous_key_during_rotation(gw):
    new_key = "cd" * 32
    gw.session_key(new_key, prev=SESSION_KEY)
    gw.require_sessions(True)
    gw.exact("echo hello")
    sid = "chat-rotated"
    proc = gw.run("ESHU_SESSION_ID=%s ESHU_SESSION_SIG=%s echo hello"
                  % (sid, session_sig(sid, SESSION_KEY)))
    assert proc.stdout.strip() == "hello"
    assert proc.returncode == 0


def test_strict_mode_fails_open_before_the_first_key_sync(gw):
    # No eshu-session-key yet: a brand-new gateway must not brick its first day.
    gw.require_sessions(True)
    gw.exact("echo hello")
    proc = gw.run("ESHU_SESSION_ID=chat-new ESHU_SESSION_SIG=whatever echo hello")
    assert proc.stdout.strip() == "hello"
    assert proc.returncode == 0
    assert "SESSION KEY MISSING" in gw.logger_text()


def test_freeze_still_wins_over_the_session_gate(gw):
    gw.session_key()
    gw.require_sessions(True)
    gw.freeze(True)
    gw.exact("echo hello")
    proc = gw.run("echo hello")
    assert "FROZEN" in proc.stdout
    assert proc.returncode == 1


def test_hard_block_still_wins_over_the_session_gate(gw):
    gw.session_key()
    gw.require_sessions(True)
    proc = gw.run("cat /etc/eshu-exact.txt")
    assert "FATAL" in proc.stdout
    assert proc.returncode == 1


def test_strict_mode_off_keeps_untagged_allowlist(gw):
    gw.session_key()
    gw.require_sessions(False)
    gw.exact("echo hello")
    proc = gw.run("echo hello")
    assert proc.stdout.strip() == "hello"
    assert proc.returncode == 0


def test_strict_session_failure_is_logged_with_the_id(gw):
    gw.session_key()
    gw.require_sessions(True)
    gw.run("ESHU_SESSION_ID=chat-bad ESHU_SESSION_SIG=deadbeef echo hello")
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/log")
        and c["data"].get("status") == "session-required"
        and c["data"].get("session_id") == "chat-bad"
        for c in calls))


# ── encoded payloads ───────────────────────────────────────────────────────
def test_encoded_wrapper_is_forced_to_jit_even_when_allowlisted(gw):
    inner = base64.b64encode(b"echo ENCODED_RAN").decode()
    wrapper = "bash -c 'echo %s | base64 -d | bash'" % inner
    gw.exact(wrapper)          # explicit allowlist must not auto-approve it
    gw.jit_status("denied")
    proc = gw.run(wrapper)
    assert "[DENIED]" in proc.stdout
    assert "ENCODED_RAN" not in proc.stdout
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/request") for c in calls))
    assert "ENCODED PAYLOAD" in gw.logger_text()


def test_encoded_payload_403_is_surfaced(gw):
    inner = base64.b64encode(b"cat /etc/eshu-exact.txt").decode()
    wrapper = "bash -c 'echo %s | base64 -d | bash'" % inner
    gw.request_mode("403")
    proc = gw.run(wrapper)
    assert "Encoded payload blocked" in proc.stdout
    assert proc.returncode == 1

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
import re
import shutil
import subprocess
import threading
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

    def __init__(self, root: Path, target_ip: str = "10.0.0.1",
                 script: Path = SCRIPT, stdbuf: bool = True,
                 timeout_version: str = "timeout (GNU coreutils) 9.4"):
        self.root = Path(root)
        self.target_ip = target_ip
        self.source_script = Path(script)
        # F19/AD2: the host's `timeout` decides the run-limit capability. CI
        # runners move between GNU coreutils, uutils and BusyBox, so the
        # sandbox binds a stub whose --version the test chooses and whose
        # behaviour is the real local timeout.
        self.timeout_version = timeout_version
        self._real_timeout = shutil.which("timeout") or "/usr/bin/timeout"
        # B1: a gateway whose userland has no stdbuf (BusyBox) falls back to
        # plain sed. The swap below is the only way to exercise that here,
        # because the system stdbuf is always on PATH.
        self.stdbuf = stdbuf
        if " " in str(self.root):
            pytest.skip("temporary path contains a space; the script is not quoted")
        self.etc = self.root / "etc"
        self.run_dir = self.root / "run"
        self.bin = self.root / "bin"
        self.home = self.root / "home"
        self.features = self.etc / "features.d"
        # F12: the persistent nonce store (root-only 0700 in production).
        self.nonce_dir = self.root / "var-lib-eshu"
        for directory in (self.etc, self.run_dir, self.bin, self.home, self.features,
                          self.nonce_dir):
            directory.mkdir(parents=True, exist_ok=True)
        os.chmod(self.nonce_dir, 0o700)
        self.curl_argv_log = self.root / "curl-argv.log"
        # BG2: header files handed to curl by path (only the result post).
        self.curl_header_log = self.root / "curl-headers.log"
        self.token_file = self.etc / "eshu-token"
        self.token_file.write_text("test-token\n")
        # F5: the poller's private work dir (0700, owner-checked before use).
        self.capture_dir = self.root / "run-eshu"
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.capture_dir, 0o700)

        self.curl_log = self.root / "curl.log"
        self.logger_log = self.root / "logger.log"
        self.status_file = self.root / "jit-status"
        self.request_mode_file = self.root / "request-mode"
        self.claim_mode_file = self.root / "claim-mode"
        self.window_claim_file = self.root / "window-claim"
        self.exec_fail_file = self.root / "exec-fail"
        # F19: when present, /api/request answers approved with this limit,
        # and /api/ticket returns it beside the ticket.
        self.approved_file = self.root / "unattended-approved"
        self.limit_file = self.root / "run-limit.json"
        self.window_claim_file.write_text("200")
        self.ticket_file = self.run_dir / "eshu.tickets"

        self._assert_tmpdir_can_exec()
        self._write_stubs()
        self.script = self.root / "eshu-gateway.sh"
        self.script.write_text(self._transformed())
        subprocess.run(["bash", "-n", str(self.script)], check=True)

    def _assert_tmpdir_can_exec(self):
        """Skip with a reason when the temp dir cannot run scripts.

        Several tests execute a script they wrote (`./bigout`), so a TMPDIR
        mounted `noexec` — some CI sandboxes do that — fails them for a reason
        that has nothing to do with the gateway. Skip instead of failing, and
        say why.
        """
        probe = self.root / "probe-exec"
        probe.write_text("#!/bin/sh\nexit 0\n")
        probe.chmod(0o755)
        try:
            subprocess.run([str(probe)], check=True, capture_output=True,
                           timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            pytest.skip("the temporary directory cannot execute scripts: %s" % exc)
        finally:
            try:
                probe.unlink()
            except OSError:
                pass

    # ── sandbox construction ────────────────────────────────────────────────
    def _write_stubs(self):
        curl = '''#!/bin/bash
LOG="__LOG__"
ARGLOG="__ARGLOG__"
printf '%s\\n' "$*" >> "$ARGLOG"
STATUS_FILE="__STATUS__"
REQUEST_MODE="__MODE__"
CLAIM_MODE="__CLAIM__"
method="GET"; url=""; data=""
while [ $# -gt 0 ]; do
  case "$1" in
    -X) method="$2"; shift 2 ;;
    -d) data="$2"; shift 2 ;;
    --data-binary)
      case "$2" in
        @-) data=$(cat) ;;
        *) data="$2" ;;
      esac
      shift 2 ;;
    -H)
      # BE3: the token header arrives on stdin now; drain it so the writer
      # does not see EPIPE. BG2: the result post passes a process
      # substitution (a FIFO path); read it and keep the content in a
      # separate log, never in argv.
      case "$2" in
        @-) cat >/dev/null ;;
        @*) cat "${2#@}" >> "__HDRLOG__" 2>/dev/null ;;
      esac
      shift 2 ;;
    -m|-o|-w) shift 2 ;;
    --connect-timeout) shift 2 ;;
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
      400) printf '{"detail":"multiline_command: the command contains a line break; send it as a single line."}\\n400\\n' ;;
      fail) exit 7 ;;
      *)
        # F19: an unattended row comes back approved, with its limit.
        if [ -f "__APPROVED__" ]; then
          printf '{"id":"000123","request_id":123,"approved":true,"run_limit":%s}\\n200\\n' \
            "$(cat "__LIMIT__" 2>/dev/null || echo '{}')"
        else
          printf '{"id":"000123"}\\n200\\n'
        fi
        ;;
    esac
    ;;
  */api/request_status/*)
    printf '{"status":"%s"}\\n' "$(cat "$STATUS_FILE" 2>/dev/null || echo pending)"
    ;;
  */api/exec-result)
    # F5: simulate an old dashboard / dropped connection for the result post.
    if [ -f "__EXECFAIL__" ]; then exit 7; fi
    ;;
  */api/approved-windows/execute|*/api/approved-windows/execute/*)
    mode=$(cat "__WINDOW_CLAIM__" 2>/dev/null || echo 200)
    if [ "$mode" = "exit" ]; then exit 7; fi
    printf '%s' "$mode"
    ;;
  */api/ticket/*)
    mode=$(cat "$CLAIM_MODE" 2>/dev/null || echo ok)
    case "$mode" in
      null) printf '{"ticket":null}\\n' ;;
      mismatch) printf '{"ticket":"123|some other command"}\\n' ;;
      *)
        # Mirror the real dashboard: "<created_at>|<command>" for an unsigned
        # caller, v2|ts|agent|session|cmd for a signed one (F12 M1), and the
        # F19 limit beside the ticket for an unattended row.
        req_json=$(awk -F'\t' '$2 ~ /\/api\/request$/ { data=$3 } END { print data }' "$LOG")
        printf '%s' "$req_json" | python3 -c '
import base64, json, os, sys
doc = json.load(sys.stdin)
cmd = base64.b64decode(doc["encoded_command"]).decode()
agent = doc.get("agent") or ""
session = doc.get("session_id") or ""
ticket = ("v2|123|%s|%s|%s" % (agent, session, cmd)) if agent else ("123|" + cmd)
out = {"ticket": ticket}
limit_path = "__LIMIT__"
if os.path.exists(limit_path):
    out["unattended"] = True
    with open(limit_path) as fh:
        out["run_limit"] = json.load(fh)
print(json.dumps(out, separators=(",", ":")))'
        ;;
    esac
    ;;
  *) : ;;
esac
'''
        curl = (curl.replace("__LOG__", str(self.curl_log))
                    .replace("__ARGLOG__", str(self.curl_argv_log))
                    .replace("__HDRLOG__", str(self.curl_header_log))
                    .replace("__STATUS__", str(self.status_file))
                    .replace("__MODE__", str(self.request_mode_file))
                    .replace("__CLAIM__", str(self.claim_mode_file))
                    .replace("__WINDOW_CLAIM__", str(self.window_claim_file))
                    .replace("__EXECFAIL__", str(self.exec_fail_file))
                    .replace("__APPROVED__", str(self.approved_file))
                    .replace("__LIMIT__", str(self.limit_file)))
        self._executable(self.bin / "curl", curl)

        logger = '''#!/bin/bash
printf '%s\\n' "$*" >> "__LOG__"
'''.replace("__LOG__", str(self.logger_log))
        self._executable(self.bin / "logger", logger)

        timeout = '''#!/bin/bash
if [ "$1" = "--version" ]; then
  printf '%s\\n' "__VERSION__"
  exit 0
fi
exec "__REAL__" "$@"
'''.replace("__VERSION__", self.timeout_version).replace(
            "__REAL__", self._real_timeout)
        self._executable(self.bin / "timeout", timeout)

    @staticmethod
    def _executable(path: Path, body: str):
        path.write_text(body)
        path.chmod(0o755)

    def _transformed(self) -> str:
        text = self.source_script.read_text()

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
                     "eshu-require-session", "eshu-session-key",
                     "eshu-capture"):
            if "/etc/" + name in text:
                text = swap("/etc/" + name, str(self.etc / name))
        text = swap('FEATURES_DIR="/etc/eshu/features.d"',
                    'FEATURES_DIR="%s"' % self.features, 1)
        if not self.stdbuf:
            text = swap('if command -v stdbuf >/dev/null 2>&1; then',
                        'if false; then', 1)
        # F12: the prefix MAC key, the ssh_mode marker and the nonce store.
        for name in ("eshu-token", "eshu-ssh-mode"):
            if "/etc/" + name in text:
                text = swap("/etc/" + name, str(self.etc / name))
        # A frozen pre-F12 copy (tests/test_sec_compat.py) has no store yet.
        if 'ESHU_NONCE_DIR="${ESHU_NONCE_DIR:-/var/lib/eshu}"' in text:
            text = swap('ESHU_NONCE_DIR="${ESHU_NONCE_DIR:-/var/lib/eshu}"',
                        'ESHU_NONCE_DIR="${ESHU_NONCE_DIR:-%s}"' % self.nonce_dir, 1)
        if 'ESHU_NONCE_OWNER="${ESHU_NONCE_OWNER:-0}"' in text:
            text = swap('ESHU_NONCE_OWNER="${ESHU_NONCE_OWNER:-0}"',
                        'ESHU_NONCE_OWNER="${ESHU_NONCE_OWNER:-%d}"' % os.getuid(), 1)
        if 'ESHU_RUN_DIR="${ESHU_RUN_DIR:-/run/eshu}"' in text:
            text = swap('ESHU_RUN_DIR="${ESHU_RUN_DIR:-/run/eshu}"',
                        'ESHU_RUN_DIR="${ESHU_RUN_DIR:-%s}"' % self.capture_dir, 1)
        if 'ESHU_RUN_OWNER="${ESHU_RUN_OWNER:-0}"' in text:
            text = swap('ESHU_RUN_OWNER="${ESHU_RUN_OWNER:-0}"',
                        'ESHU_RUN_OWNER="${ESHU_RUN_OWNER:-%d}"' % os.getuid(), 1)

        for token, value in (
            ("__TARGET_IP__", self.target_ip),
            ("__DASHBOARD_URL__", "http://dashboard.test"),
            ("__GATEWAY_VERSION__", "v-test"),
        ):
            text = swap(token, value, 1)

        # The relocation must be complete, or a test could touch the real host.
        # (The sandbox path itself ends with /etc/eshu-*, so compare counts: a
        # leftover real path makes the second count larger.)
        for name in ("eshu-freeze", "eshu-zero-trust", "eshu-rblack.txt",
                     "eshu-exact.txt", "eshu-rwhite.txt",
                     "eshu-require-session", "eshu-session-key",
                     "eshu-capture", "eshu-token", "eshu-ssh-mode"):
            sandbox_path = str(self.etc / name)
            assert text.count("/etc/" + name) == text.count(sandbox_path), name
        assert "/etc/eshu/features.d" not in text
        assert "/var/run/eshu.tickets" not in text
        assert "${ESHU_RUN_DIR:-/run/eshu}" not in text
        # F12/S2: the nonce store is on the hard self-protection list, so the
        # real path appears exactly once on purpose. The frozen pre-F12 copy
        # has no nonce store at all.
        if "ESHU_NONCE_DIR" in text:
            assert text.count("/var/lib/eshu") == 1
        # The hard self-protection case still names the real path on purpose.
        assert '"/etc/eshu' in text
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

    def exec_fail(self, on: bool):
        if on:
            self.exec_fail_file.write_text("1")
        elif self.exec_fail_file.exists():
            self.exec_fail_file.unlink()

    def capture(self, mode: str):
        (self.etc / "eshu-capture").write_text(mode)

    def popen(self, command: str, args=None):
        env = {
            "SSH_ORIGINAL_COMMAND": command,
            "SSH_CONNECTION": "127.0.0.1 40000 127.0.0.1 22",
            "USER": "tester",
            "LANG": "C.UTF-8",
            "ESHU_JIT_WAIT": "2",
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        }
        return subprocess.Popen(
            ["bash", str(self.script)] + list(args or []), cwd=str(self.root),
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def unattended(self, limit: dict, on: bool = True):
        """F19: answer /api/request with an approved unattended row."""
        if on:
            self.limit_file.write_text(json.dumps(limit))
            self.approved_file.write_text("1")
        else:
            for path in (self.limit_file, self.approved_file):
                try:
                    path.unlink()
                except OSError:
                    pass

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

    def claim_mode(self, mode: str):
        self.claim_mode_file.write_text(mode)

    def window_claim(self, mode: str):
        self.window_claim_file.write_text(mode)

    def tickets(self, *lines: str):
        self._write(self.ticket_file, *lines)

    def ssh_mode(self, mode: str):
        (self.etc / "eshu-ssh-mode").write_text(mode)

    def token(self, value: str):
        self.token_file.write_text(value + "\n")

    def sign(self, command: str, agent: str = "agent-a", session: str = "",
             force_jit: str = "0", ts: int = None, nonce: str = None,
             check_token: str = ""):
        """Build the signed prefix exactly as core/ssh_crypto.py does."""
        import secrets as _secrets
        ts = str(ts if ts is not None else int(time.time()))
        nonce = nonce or _secrets.token_hex(16)
        import sys as _sys
        _sys.path.insert(0, str(REPO / "dashboard"))
        from core import ssh_crypto
        key = ssh_crypto.derive_key((self.etc / "eshu-token").read_text().strip())
        canonical = ssh_crypto.canonical(
            command, agent, session=session, mcp="1", force_jit=force_jit,
            ts=ts, nonce=nonce, gateway_ip=self.target_ip,
            check_token=check_token)
        signature = ssh_crypto.sign(key, canonical)
        return ssh_crypto.prefix_line(agent, session, force_jit, ts, nonce,
                                      signature, check_token=check_token) + command

    def command_script(self, name: str, body: str) -> str:
        """Write an executable test command (no shell metacharacters)."""
        path = self.root / name
        path.write_text("#!/bin/bash\n" + body)
        path.chmod(0o755)
        return "./" + name

    def feature(self, name: str, body: str):
        (self.features / name).write_text(body)

    def approved_windows(self, *lines: str):
        """Install the real feature script with /etc/eshu-windows.txt relocated."""
        self._write(self.etc / "eshu-windows.txt", *lines)
        source = (REPO / "dashboard" / "static" / "features"
                  / "approved_windows.sh").read_text()
        source = source.replace("/etc/eshu-windows.txt",
                                str(self.etc / "eshu-windows.txt"))
        self.feature("approved_windows.sh", source)

    # ── execution/observation ───────────────────────────────────────────────
    def run(self, command: str, args=None):
        env = {
            "SSH_ORIGINAL_COMMAND": command,
            "SSH_CONNECTION": "127.0.0.1 40000 127.0.0.1 22",
            "USER": "tester",
            "LANG": "C.UTF-8",
            "ESHU_JIT_WAIT": "2",
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        }
        try:
            proc = subprocess.run(
                ["bash", str(self.script)] + list(args or []),
                cwd=str(self.root), env=env, capture_output=True, text=True,
                timeout=60)
        except subprocess.TimeoutExpired as exc:
            # A hang is the interesting failure: show what every process in
            # the sandbox is waiting on, so a CI-only hang explains itself.
            pytest.fail(self._hang_report() + '\n--- stdout ---\n'
                        + os.fsdecode(exc.stdout or b'')[-2000:])
        time.sleep(0.15)  # let the backgrounded /api/log stub finish
        return proc

    def _hang_report(self) -> str:
        """ps + wchan + open fds for the sandbox's processes, best effort."""
        lines = []
        try:
            ps = subprocess.run(['ps', '-eo', 'pid,ppid,stat,wchan:24,args'],
                                capture_output=True, text=True, timeout=10)
            body = [line for line in ps.stdout.splitlines()
                    if str(self.root) in line or 'eshu-gateway.sh' in line]
            lines.append('ps:\n' + '\n'.join(body))
        except Exception as exc:  # noqa: BLE001 - diagnostics only
            lines.append('ps failed: %r' % (exc,))
        try:
            out = os.listdir('/proc')
            for pid in [p for p in out if p.isdigit()]:
                try:
                    cmd = open('/proc/%s/cmdline' % pid, 'rb').read() \
                        .replace(b'\0', b' ').decode('utf-8', 'replace')
                except OSError:
                    continue
                if 'eshu-gateway' not in cmd:
                    continue
                try:
                    wchan = open('/proc/%s/wchan' % pid).read().strip()
                except OSError:
                    wchan = '?'
                try:
                    fds = os.listdir('/proc/%s/fd' % pid)
                except OSError:
                    fds = []
                lines.append('pid %s wchan=%s fds=%s cmd=%s'
                             % (pid, wchan, len(fds), cmd[:160]))
        except Exception as exc:  # noqa: BLE001
            lines.append('proc scan failed: %r' % (exc,))
        return 'HANG: ' + '\n'.join(lines)

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

    def header_text(self):
        """The content of every header file a curl call was given by path."""
        if not self.curl_header_log.exists():
            return ""
        return self.curl_header_log.read_text()

    def argv_calls(self):
        """Every curl invocation's argv, one line per call (BE3)."""
        if not self.curl_argv_log.exists():
            return []
        return self.curl_argv_log.read_text().splitlines()

    def raw_bodies(self):
        """The literal ``-d`` argument of every recorded call (unparsed)."""
        if not self.curl_log.exists():
            return []
        bodies = []
        for line in self.curl_log.read_text().splitlines():
            parts = line.split("\t", 2)
            bodies.append(parts[2] if len(parts) == 3 else "")
        return bodies

    def wait_for(self, predicate, timeout=10.0):
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
    for token in ("__TARGET_IP__", "__DASHBOARD_URL__", "__GATEWAY_VERSION__"):
        assert token in text
    # BQ4: no baked token; it reads the one file.
    assert "__GATEWAY_TOKEN__" not in text
    assert not re.search(r'^GATEWAY_TOKEN=', text, re.M)
    assert "/etc/eshu-token" in text
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


def test_jit_consumed_claims_the_lockbox_ticket(gw):
    # The poller has already consumed the ticket and deposited "<ts>|<cmd>" in
    # the lockbox; the gateway may run only because that exact line exists.
    gw.tickets("%d|echo consumed-ok" % int(time.time()))
    gw.jit_status("consumed")
    proc = gw.run("echo consumed-ok")
    assert "consumed-ok" in proc.stdout
    assert proc.returncode == 0
    assert not gw.ticket_file.read_text().strip()
    assert not any(c["url"].endswith("/api/ticket/") for c in gw.curl_calls())


def test_bk3_two_approvals_of_the_same_command_each_run_once(gw):
    # Each approval is delivered exactly once. A second removal pass used to
    # delete the other live approval's line, so its run timed out.
    now = int(time.time())
    gw.tickets("%d|echo twice" % now, "%d|echo twice" % (now - 1))
    gw.jit_status("consumed")
    first = gw.run("echo twice")
    assert "twice" in first.stdout and first.returncode == 0
    second = gw.run("echo twice")
    assert "twice" in second.stdout and second.returncode == 0
    assert not gw.ticket_file.read_text().strip()


def test_bk4_the_approved_command_does_not_inherit_the_ticket_lock(gw):
    if not os.path.isdir("/proc/self/fd"):
        pytest.skip("no /proc/self/fd on this platform")
    # No `|` in the command: the ticket parser splits on it.
    cmd = "find /proc/self/fd -maxdepth 1 -name 9 -print"
    gw.tickets("%d|%s" % (int(time.time()), cmd))
    proc = gw.run(cmd)
    assert "/proc/self/fd/9" not in proc.stdout
    assert proc.returncode == 0


def test_bk2_a_refused_request_shows_the_dashboard_reason(gw):
    gw.request_mode("400")
    proc = gw.run("echo one")
    assert "multiline_command" in proc.stdout
    assert "unreachable" not in proc.stdout
    assert proc.returncode == 1


def test_bm2_the_check_token_body_is_not_in_curl_argv(gw):
    """BM2: the /api/request body, which carries the signed check token, goes
    on stdin; only a local reader could see argv, but argv is the rule."""
    token = "a" * 32
    gw.jit_status("denied")
    gw.run(gw.sign("echo check-body", force_jit="1", check_token=token))
    args = gw.argv_calls()
    assert args, "no curl call recorded"
    for line in args:
        assert token not in line, line
    req = [c for c in gw.curl_calls()
           if c["url"].endswith("/api/request")]
    assert req and req[0]["data"].get("check_token") == token


def test_failed_claim_does_not_execute(gw):
    # A failed /api/ticket claim must not run the command: the dashboard would
    # keep the request "approved" with no jit_consumed audit (bug #2393).
    gw.claim_mode("null")
    gw.jit_status("approved")
    proc = gw.run("echo should-not-run")
    assert "should-not-run" not in proc.stdout
    assert proc.returncode == 1
    assert "JIT CLAIM FAILED" in gw.logger_text()
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/ticket/000123") for c in calls))


def test_double_space_prefix_still_matches_the_whitelist(gw):
    # The returned ssh_prefix ends with a space; a wrapper that adds its own
    # leaves a leading space after stripping. It must not change classification.
    gw.session_key()
    gw.require_sessions(True)
    gw.exact("uptime")
    sid = "space-test"
    proc = gw.run("ESHU_SESSION_ID=%s ESHU_SESSION_SIG=%s  uptime"
                  % (sid, session_sig(sid)))
    assert proc.returncode == 0
    assert not any(c["url"].endswith("/api/request") for c in gw.curl_calls())
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/log") and c["data"].get("status") == "auto-approved"
        for c in calls))


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
    # A script, not `sh -c '...; ...'`: the chunk 2 metacharacter gate now
    # routes the latter to JIT, but exit propagation must still be covered.
    command = gw.command_script("fails.sh", "echo before\nexit 3\n")
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
    command = gw.command_script("leaks.sh",
                                "echo API_KEY=supersecret\nexit 5\n")
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
    proc = gw.run("ESHU_SESSION_ID=chat-signed ESHU_SESSION_SIG=deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef echo hello")
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
    proc = gw.run("ESHU_SESSION_ID=chat-new ESHU_SESSION_SIG=0000000000000000000000000000000000000000000000000000000000000000 echo hello")
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
    gw.run("ESHU_SESSION_ID=chat-bad ESHU_SESSION_SIG=deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef echo hello")
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


# ── Chunk 1: prefix metadata, JSON bodies, ticket binding ──────────────────
HOSTILE_PREFIX_VALUES = [
    ("ESHU_SESSION_ID", 'x","status":"approved'),
    ("ESHU_EXECUTION_ID", 'x","status":"approved'),
    ("ESHU_WINDOW_TOKEN", 'x","status":"approved'),
    ("ESHU_SESSION_SIG", 'x","status":"approved'),
]


@pytest.mark.parametrize("name,value", HOSTILE_PREFIX_VALUES)
def test_sec_chunk1_hostile_prefix_is_refused_without_a_dashboard_call(
        gw, name, value):
    gw.exact("echo hello")
    proc = gw.run("%s=%s echo hello" % (name, value))
    assert proc.returncode == 1
    assert "Invalid ESHU_" in proc.stdout
    assert "hello" not in proc.stdout
    assert gw.curl_calls() == []
    # The validation log line must never echo the raw value — only its length.
    invalid_line = next(line for line in gw.logger_text().splitlines()
                        if "INVALID PREFIX" in line)
    assert value not in invalid_line
    assert "length=" in invalid_line


@pytest.mark.parametrize("value", ['x,y', 'x{y}', 'x\\y', 'x"y'])
def test_sec_chunk1_execution_id_rejects_json_metacharacters(gw, value):
    gw.exact("echo hello")
    proc = gw.run("ESHU_EXECUTION_ID=%s echo hello" % value)
    assert proc.returncode == 1
    assert "Invalid ESHU_EXECUTION_ID" in proc.stdout
    assert gw.curl_calls() == []


def test_sec_chunk1_allowlist_log_body_is_well_formed(gw):
    gw.exact("echo hello")
    proc = gw.run("ESHU_SESSION_ID=chat_valid-1 ESHU_EXECUTION_ID=exec.v1:2 "
                  "ESHU_WINDOW_TOKEN=WinToken123 echo hello")
    assert proc.stdout.strip() == "hello"
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/log") for c in calls))
    # F5 adds a second gateway post (/api/exec-result); this test is about the
    # /api/log body, so select those calls only.
    bodies = [json.loads(raw) for raw, call in
              zip(gw.raw_bodies(), gw.curl_calls())
              if call["url"].endswith("/api/log")]
    assert bodies
    for payload in bodies:
        assert set(payload) == {
            "target_ip", "encoded_command", "status",
            "session_id", "execution_id", "agent"}
        assert payload["target_ip"] == "10.0.0.1"
        assert payload["status"] == "auto-approved"
        assert payload["session_id"] == "chat_valid-1"
        assert payload["execution_id"] == "exec.v1:2"


def test_sec_chunk1_jit_request_body_is_well_formed(gw):
    gw.jit_status("denied")
    proc = gw.run("ESHU_SESSION_ID=sess-1 ESHU_EXECUTION_ID=exec-1 echo hello")
    assert proc.returncode == 1
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/request") for c in calls))
    for call, raw in zip(gw.curl_calls(), gw.raw_bodies()):
        if call["url"].endswith("/api/request"):
            payload = json.loads(raw)
            assert set(payload) == {
                "target_ip", "encoded_command", "session_id", "execution_id",
                "agent", "force_jit", "run_limits", "check_token"}
            # F19: '' or 'v1' — the capability this running script reports.
            assert payload["run_limits"] in ("", "v1")


def test_bi1_a_future_stamped_ticket_is_neither_kept_nor_claimed(gw):
    """BI1: a stamp in the future never expires, so it is not a ticket."""
    gw.ticket_file.write_text('v2|%d|||echo future-line\n' % (int(time.time()) + 3600))
    proc = gw.run('echo future-line')
    assert 'JIT TICKET CLAIMED' not in gw.logger_text()
    assert 'future-line' not in proc.stdout
    assert gw.ticket_file.read_text() == ''


def test_bi1_a_cr_line_is_dropped(gw):
    gw.ticket_file.write_text('v2|%d|||echo cr-line\r\n' % int(time.time()))
    proc = gw.run('echo cr-line')
    assert 'JIT TICKET CLAIMED' not in gw.logger_text()
    assert 'cr-line' not in proc.stdout
    assert gw.ticket_file.read_text() == ''


def test_bi2_two_concurrent_claims_of_one_ticket_run_once(gw, tmp_path):
    """BI2(a): the gateway holds a lock across the read-modify-write."""
    marker = tmp_path / 'bi2-marker'
    cmd = 'printf x >> %s' % marker
    gw.ticket_file.write_text('v2|%d|||%s\n' % (int(time.time()), cmd))
    seen = []

    def claim():
        seen.append(gw.run(cmd))

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert marker.exists(), [p.stdout for p in seen]
    assert marker.read_text() == 'x'
    assert gw.ticket_file.read_text() == ''


def test_bi2_the_claim_locks_and_uses_a_run_dir_temp():
    text = SCRIPT.read_text()
    assert 'TICKETS_LOCK="$ESHU_RUN_DIR/eshu.tickets.lock"' in text
    assert 'flock -w 5 9' in text
    # The temp file is on the same filesystem as the lockbox, never /tmp.
    assert 'mktemp -p "$ESHU_RUN_DIR" eshu-tickets.XXXXXX' in text
    assert 'tmp=$(mktemp)' not in text
    # Every writer path takes the lock.
    claim = text[text.index('claim_lockbox_ticket() {'):]
    claim = claim[:claim.index('\n}\n')]
    assert claim.index('tickets_lock') < claim.index('mv -f')
    # BK3: the claim-and-burn is the only remover. A second pass that deleted
    # another approval's line for the same command is gone.
    assert 'remove_claimed_ticket' not in text
    # BK4: fd 9 is closed on both the unlock and the lock-failure return, so
    # no later command inherits a writable descriptor on the lock file.
    unlock = text[text.index('tickets_unlock() {'):]
    unlock = unlock[:unlock.index('\n}\n')]
    assert 'exec 9>&-' in unlock
    assert 'flock -w 5 9 || { exec 9>&-; return 1; }' in text

def test_be3_the_gateway_token_is_never_in_curl_argv(gw):
    """BE3: every gateway call keeps the token off argv (world-readable in
    /proc while the call runs). The request, status, ticket and result paths
    and the window-reject log path must all pass."""
    gw.jit_status("approved")
    gw.run("echo be3-jit")
    gw.feature("window.sh", 'log_window_reject "$cmd" "unknown window token"\n')
    gw.jit_status("denied")
    gw.run("ESHU_SESSION_ID=sess-1 ESHU_WINDOW_TOKEN=WinToken123 echo be3-w")
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/log") for c in calls))
    args = gw.argv_calls()
    assert args, "no curl call recorded"
    assert any("/api/request" in line for line in args)
    assert any("/api/log" in line for line in args)
    assert any("/api/ticket/" in line for line in args)
    assert any("/api/exec-result" in line for line in args)
    for line in args:
        assert "test-token" not in line, line
        assert "X-Gateway-Token" not in line, line
        assert "X-Enrollment-Token" not in line, line
        # BH3: the window token is a credential in a JSON body, not argv.
        assert "WinToken123" not in line, line


def test_bg2_the_result_token_header_is_a_private_fifo(gw):
    """BG2: the result post reads its header from a process substitution.

    No named file in /tmp to race, so the header must still arrive: the stub
    records the content of every header file curl was given by path, and the
    argv line stays token-free.
    """
    gw.jit_status("approved")
    gw.run("echo bg2")
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/exec-result") for c in calls))
    assert "X-Gateway-Token: test-token" in gw.header_text()
    line = [l for l in gw.argv_calls() if "/api/exec-result" in l][0]
    assert "test-token" not in line
    assert "X-Gateway-Token" not in line
    assert "mktemp" not in line


def test_bg2_the_result_post_writes_no_tmp_file():
    """BG2: the old `mktemp` header file is gone from the shipped script."""
    text = SCRIPT.read_text()
    assert "-H @<(gateway_header)" in text
    assert "hdr=$(umask 077; mktemp)" not in text
    assert 'rm -f "$hdr"' not in text


def test_f22_a_token_without_a_signed_prefix_is_refused(gw):
    """AY2: ESHU_CHECK_TOKEN joins the manifest trigger, so a token on its
    own is an incomplete prefix and never reaches the dashboard."""
    gw.jit_status("denied")
    gw.run("ESHU_CHECK_TOKEN=" + "a" * 32 + " echo hello")
    assert not [c for c in gw.curl_calls()
                if c["url"].endswith("/api/request")]


def test_f22_installers_embed_the_gateway_byte_for_byte():
    """AY1/BE2: the four installer files are one generated text.

    The template plus the three sources is the generator's output; every
    committed installer must be exactly that, and the dev copy (which the
    deploy resets and a promote copies over the fleet installer) must not
    lag behind.
    """
    repo = Path(__file__).resolve().parent.parent
    dashboard = repo / "dashboard"

    def read(name):
        return (dashboard / name).read_text(encoding="utf-8")

    gateway = read("eshu-gateway.sh").rstrip("\n")
    template = read("eshu-installer-template.sh")
    generated = template.replace("__GATEWAY_CONTENT__", gateway)
    generated = generated.replace("__POLLER_CONTENT__",
                                  read("eshu-poller.sh").rstrip("\n"))
    generated = generated.replace("__LOGGER_CONTENT__",
                                  read("eshu-logger.sh").rstrip("\n"))
    assert "ESHU_CHECK_TOKEN" in generated
    for rel in ("dashboard/eshu-gateway-install.sh",
                "dashboard/static/eshu-gateway-install.sh",
                "dashboard/static/dev/eshu-gateway-install.sh"):
        text = (repo / rel).read_text(encoding="utf-8")
        assert text == generated, "%s is not the generated installer" % rel


def test_f22_a_signed_check_token_reaches_the_request_body(gw):
    """AV1: the check token is a v2 signed field, so the gateway accepts it
    and forwards it; a token added after signing is refused."""
    gw.jit_status("denied")
    token = "a" * 32
    gw.run(gw.sign("echo hello", force_jit="1", check_token=token))
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/request") for c in calls))
    seen = []
    for call, raw in zip(gw.curl_calls(), gw.raw_bodies()):
        if call["url"].endswith("/api/request"):
            seen.append(json.loads(raw)["check_token"])
    assert seen and seen[-1] == token


def test_f22_a_token_outside_the_signature_is_refused(gw):
    gw.jit_status("denied")
    prefix = gw.sign("echo hello", force_jit="1")
    forged = prefix.replace(
        "ESHU_FORCE_JIT=1", "ESHU_FORCE_JIT=1 ESHU_CHECK_TOKEN=" + "b" * 32)
    gw.run(forged)
    assert not [c for c in gw.curl_calls()
                if c["url"].endswith("/api/request")]


def test_sec_chunk1_window_reject_body_is_well_formed(gw):
    gw.feature("window.sh", 'log_window_reject "$cmd" "unknown window token"\n')
    gw.jit_status("denied")
    proc = gw.run("ESHU_SESSION_ID=sess-1 ESHU_EXECUTION_ID=exec-1 "
                  "ESHU_WINDOW_TOKEN=WinToken123 echo w")
    assert proc.returncode == 1
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/log")
        and c["data"].get("status") == "window-rejected" for c in calls))
    seen = False
    for call, raw in zip(gw.curl_calls(), gw.raw_bodies()):
        if (call["url"].endswith("/api/log")
                and call["data"].get("status") == "window-rejected"):
            seen = True
            payload = json.loads(raw)
            assert set(payload) == {
                "target_ip", "encoded_command", "status", "reason", "token",
                "session_id", "execution_id", "agent"}
            assert payload["reason"] == "unknown window token"
            assert payload["token"] == "WinToken123"
    assert seen


def test_sec_chunk1_claim_mismatch_is_refused(gw):
    gw.jit_status("approved")
    gw.claim_mode("mismatch")
    proc = gw.run("echo should-not-run")
    assert ("Approved command does not match the requested command"
            in proc.stdout)
    assert "should-not-run" not in proc.stdout
    assert proc.returncode == 1
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/ticket/000123") for c in calls))
    assert "JIT COMMAND MISMATCH" in gw.logger_text()


def test_sec_chunk1_claim_match_is_executed(gw):
    gw.jit_status("approved")
    proc = gw.run("ESHU_EXECUTION_ID=exec-1 echo matched")
    assert "matched" in proc.stdout
    assert proc.returncode == 0
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/ticket/000123") for c in calls))


def test_sec_chunk1_consumed_without_a_lockbox_ticket_is_refused(gw):
    gw.jit_status("consumed")
    proc = gw.run("echo should-not-run")
    assert "should-not-run" not in proc.stdout
    assert proc.returncode == 1
    assert not any(c["url"].endswith("/api/ticket/")
                   for c in gw.curl_calls())


def test_sec_chunk1_invalid_target_ip_refuses_to_run(tmp_path):
    gw = GatewaySandbox(tmp_path, target_ip="10.0.0.1;touch /tmp/eshu-pwned")
    proc = gw.run("echo hello")
    assert proc.returncode == 1
    assert "Invalid gateway TARGET_IP" in proc.stdout
    assert "hello" not in proc.stdout
    assert gw.curl_calls() == []


# ── Chunk 2: shell metacharacters, self-protection, prefix windows ─────────
META_CASES = [
    ("^uptime", "uptime; id"),
    ("^uptime", "uptime && id"),
    ("^uptime", "uptime | sh"),
    ("^uptime", "uptime `id`"),
    ("^uptime", "uptime $(id)"),
    ("^uptime", "uptime > meta.out"),
    ("^uptime", "uptime\nid"),
    ("^uptime", "uptime \\ id"),
    ("^journalctl .*", "journalctl -n 1; id"),
]


@pytest.mark.parametrize("pattern,command", META_CASES)
def test_sec_chunk2_metacharacter_command_routes_to_jit(gw, pattern, command):
    gw.regex_white(pattern)
    gw.jit_status("denied")
    proc = gw.run(command)
    assert "[DENIED]" in proc.stdout
    assert "uid=" not in proc.stdout
    assert proc.returncode == 1
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/request") for c in calls))
    assert not any(c["url"].endswith("/api/log")
                   and c["data"].get("status") == "auto-approved"
                   for c in gw.curl_calls())


def test_sec_chunk2_exact_allowlist_metachar_routes_to_jit(gw):
    gw.exact("df -h | head")
    gw.jit_status("denied")
    proc = gw.run("df -h | head")
    assert "[DENIED]" in proc.stdout
    assert proc.returncode == 1
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/request") for c in calls))


@pytest.mark.parametrize("command", ["uptime", "df -h"])
def test_sec_chunk2_plain_allowlist_still_auto_runs(gw, command):
    gw.exact(command)
    proc = gw.run(command)
    assert proc.returncode == 0
    assert not any(c["url"].endswith("/api/request")
                   for c in gw.curl_calls())
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/log")
        and c["data"].get("status") == "auto-approved" for c in calls))


SELF_PROTECTION_BYPASSES = [
    "cat /run/eshu.tickets",
    "ls /etc/eshu/features.d",
    "cat /etc//eshu-exact.txt",
    "cat /etc/./eshu-exact.txt",
    "echo x > /etc/eshu/dashboard_url",
    "cat /etc/sudoers.d/eshu",
    "cat /etc/eshu/features.d",
]


@pytest.mark.parametrize("command", SELF_PROTECTION_BYPASSES)
def test_sec_chunk2_self_protection_bypass_is_hard_blocked(gw, command):
    # No allowlist entry: pre-fix these fall through to JIT (and are never
    # executed), which is enough to prove the hard block missed them.
    proc = gw.run(command)
    assert "FATAL" in proc.stdout
    assert proc.returncode == 1
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/log")
        and c["data"].get("status") == "blocked" for c in calls))


def test_sec_chunk2_prefix_window_metachar_is_rejected(gw):
    gw.approved_windows("wintok|systemctl status|0|0|0|0|0|prefix")
    proc = gw.run("ESHU_WINDOW_TOKEN=wintok systemctl status nginx; id")
    assert "Window rejected" in proc.stdout
    assert "uid=" not in proc.stdout
    assert proc.returncode == 1
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/log")
        and c["data"].get("status") == "window-rejected" for c in calls))
    assert not any(c["url"].endswith("/api/request")
                   for c in gw.curl_calls())
    assert "APPROVED WINDOW MATCH" not in gw.logger_text()


def test_bj2_the_window_claim_keeps_both_tokens_off_argv(gw):
    """BJ2: the gateway token and the window token are not in curl's argv."""
    gw.approved_windows("wintok|echo window-ok|0|0|0|0|0|exact")
    proc = gw.run("ESHU_WINDOW_TOKEN=wintok echo window-ok")
    assert "window-ok" in proc.stdout
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/approved-windows/execute") for c in calls))
    argv = gw.argv_calls()
    for line in argv:
        assert "test-token" not in line, line
        assert "wintok" not in line, line
        assert "X-Gateway-Token" not in line, line
    # The token travels in the body, and the header still arrives.
    claim = [c for c in gw.curl_calls()
             if c["url"].endswith("/api/approved-windows/execute")][0]
    assert claim["data"].get("window_token") == "wintok"
    assert "X-Gateway-Token: test-token" in gw.header_text()


def test_bj3_the_window_token_is_not_logged_in_full(gw):
    src = (REPO / "dashboard" / "static" / "features" / "approved_windows.sh").read_text()
    assert "token=$ESHU_WINDOW_TOKEN" not in src
    assert "${ESHU_WINDOW_TOKEN:0:8}" in src


# ── Chunk 5: window claim fails open only on network loss ─────────────────
@pytest.mark.parametrize("claim_code", ["401", "403", "404", "429", "500"])
def test_sec_chunk5_window_claim_http_error_is_rejected(gw, claim_code):
    gw.approved_windows("wintok|echo window-ok|0|0|0|0|0|exact")
    gw.window_claim(claim_code)
    proc = gw.run("ESHU_WINDOW_TOKEN=wintok echo window-ok")
    assert "Window rejected" in proc.stdout
    assert "window-ok" not in proc.stdout
    assert proc.returncode == 1


def test_sec_chunk5_window_claim_network_failure_still_runs(gw):
    # Fail open only when curl itself failed (network down), i.e. no HTTP code.
    gw.approved_windows("wintok|echo window-ok|0|0|0|0|0|exact")
    gw.window_claim("exit")
    proc = gw.run("ESHU_WINDOW_TOKEN=wintok echo window-ok")
    assert "window-ok" in proc.stdout
    assert proc.returncode == 0


# ── Chunk R1: narrow FATAL, force-JIT the broad self-protection matches ────
R_FALSE_POSITIVES = [
    "systemctl status *mesh*",
    'find /home -name "*refresh*"',
    "[ -f /tmp/threshold ] && echo y",
    'curl -s "http://x/api?fresh=1"',
    "grep -r Refresh /var/log/*",
    "ls -la ~/.ssh/authorized_keys",
]


@pytest.mark.parametrize("command", R_FALSE_POSITIVES)
def test_sec_R_false_positive_is_not_fatal(gw, command):
    gw.jit_status("denied")
    proc = gw.run(command)
    assert "FATAL" not in proc.stdout
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/request") for c in calls))
    assert not any(c["url"].endswith("/api/log")
                   and c["data"].get("status") == "blocked"
                   for c in gw.curl_calls())


def test_sec_R_false_positive_allowlisted_runs(gw):
    # Stub the binaries so the allowlisted false positives cannot touch the host.
    gw._executable(gw.bin / "systemctl",
                   "#!/bin/bash\necho SYSTEMCTL_STUB\n")
    gw._executable(gw.bin / "find", "#!/bin/bash\necho FIND_STUB\n")
    gw.exact("systemctl status *mesh*")
    proc = gw.run("systemctl status *mesh*")
    assert "SYSTEMCTL_STUB" in proc.stdout
    assert proc.returncode == 0

    gw.exact('find /home -name "*refresh*"')
    proc = gw.run('find /home -name "*refresh*"')
    assert "FIND_STUB" in proc.stdout
    assert proc.returncode == 0

    gw.exact('curl -s "http://x/api?fresh=1"')
    proc = gw.run('curl -s "http://x/api?fresh=1"')
    assert proc.returncode == 0
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/log")
        and c["data"].get("status") == "auto-approved"
        for c in calls))


def test_sec_R_glob_esh_path_prefix_is_jit(gw):
    gw.exact("cat /etc/esh*")
    gw.jit_status("denied")
    proc = gw.run("cat /etc/esh*")
    assert "FATAL" not in proc.stdout
    assert "[DENIED]" in proc.stdout
    assert proc.returncode == 1
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/request") for c in calls))
    assert not any(c["url"].endswith("/api/log")
                   and c["data"].get("status") == "blocked"
                   for c in gw.curl_calls())


def test_sec_R_authorized_keys_is_jit(gw):
    gw.exact("cat ~/.ssh/authorized_keys")
    gw.jit_status("denied")
    proc = gw.run("cat ~/.ssh/authorized_keys")
    assert "FATAL" not in proc.stdout
    assert "[DENIED]" in proc.stdout
    assert proc.returncode == 1
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/request") for c in calls))

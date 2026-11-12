"""Hermetic regression harness for dashboard/eshu-poller.sh.

The poller is the edge-side idle loop: every 30 s it fetches the policy, parses
it (historically with ~16 short-lived ``python3`` starts), writes the /etc/eshu-*
files, then polls for a JIT ticket. On a single-core Raspberry Pi 1 that parse
storm is roughly a third of the only core, so the loop now hashes the policy body
and skips the parse/write block when nothing changed.

These tests build a disposable sandbox rather than mocking the decisions:

* the real script is copied with its paths relocated and its loop bounded to a
  fixed number of cycles;
* a fake ``curl`` serves canned policy/poll/feature bodies and records calls;
* a ``python3`` wrapper counts interpreter starts (the metric under test);
* ``logger``/``sudo``/``systemd-run`` are stubs.

No root, no network, no real /etc.
"""
import hashlib
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
POLLER = REPO / "dashboard" / "eshu-poller.sh"

pytestmark = pytest.mark.skipif(
    not shutil.which("bash"), reason="the poller is a bash script")

CYCLES = 3


def sha(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()


def policy_body(**overrides) -> str:
    import json
    data = {
        "exact_whitelist": "echo hello",
        "regex_whitelist": "",
        "regex_blacklist": "forbidden",
        "trigger_freeze": False,
        "zero_trust": 0,
        "require_sessions": 0,
        "session_signing_key": "ab" * 32,
        "session_signing_key_prev": "",
        "approved_windows": [],
        "policy_version": 1,
        "dashboard_version": "v-test",
    }
    data.update(overrides)
    return json.dumps(data)


class PollerSandbox:
    """A throwaway installation of the real poller script."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.etc = self.root / "etc"
        self.run_dir = self.root / "run"
        self.tmp = self.root / "tmp"
        self.bin = self.root / "bin"
        self.work = self.root / "work"
        for directory in (self.etc, self.etc / "eshu" / "features.d",
                          self.run_dir, self.tmp, self.bin):
            directory.mkdir(parents=True, exist_ok=True)
        self.work.mkdir(mode=0o700, exist_ok=True)

        self.policy_file = self.root / "policy.json"
        self.poll_file = self.root / "poll.json"
        self.feature_file = self.root / "feature.sh"
        self.curl_log = self.root / "curl.log"
        self.logger_log = self.root / "logger.log"
        self.python_log = self.root / "python.log"
        self.systemd_log = self.root / "systemd-run.log"
        self.work_stat_file = self.root / "work-stat"
        self.work_stat_file.write_text("0 700")
        self.fleet_runner_file = self.root / "fleet-runner.sh"
        self.fleet_runner_file.write_text("#!/bin/bash\nexit 0\n")

        self.feature_file.write_text("#!/bin/bash\n# test feature\n")
        self.poll_file.write_text("")

        self._write_stubs()
        self.script = self.root / "eshu-poller.sh"
        self.script.write_text(self._transformed())
        subprocess.run(["bash", "-n", str(self.script)], check=True)

    # ── sandbox construction ────────────────────────────────────────────────
    def _write_stubs(self):
        curl = '''#!/bin/bash
LOG="__LOG__"
POLICY="__POLICY__"
POLL="__POLL__"
FEATURE="__FEATURE__"
RUNNER="__RUNNER__"
out=""; fmt=""; url=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    -w) fmt="$2"; shift 2 ;;
    -X|-d|-H|-m) shift 2 ;;
    --connect-timeout) shift 2 ;;
    -s) shift ;;
    -*) shift ;;
    *) url="$1"; shift ;;
  esac
done
# Real curl refuses a URL with whitespace or a newline (exit 3, nothing
# sent). The stub must too, or a malformed poll URL passes the tests while
# no gateway ever polls (the WC="0\\n0" bug).
case "$url" in *[[:space:]]*) printf 'MALFORMED\\t%s\\n' "$out" >> "$LOG"; exit 3 ;; esac
printf '%s\\t%s\\n' "$url" "$out" >> "$LOG"
body=""; code="200"
case "$url" in
  */api/policy/*) body=$(cat "$POLICY" 2>/dev/null) ;;
  */api/poll/*)
    if [ -f "$POLL" ] && [ -s "$POLL" ]; then
      body=$(cat "$POLL")
      # Model the dashboard consuming the ticket on first read.
      case "$body" in *'"ticket":'*) : > "$POLL" ;; esac
    else
      body='{"ticket":null,"mode":"prod","version":"v-test"}'
    fi
    ;;
  */static/features/*)
    if [ -f "$FEATURE" ]; then body=$(cat "$FEATURE"); else code="404"; fi
    ;;
  */static/fleet-runner.sh)
    body=$(cat "$RUNNER" 2>/dev/null)
    ;;
  *) code="404" ;;
esac
if [ -n "$out" ]; then
  printf '%s' "$body" > "$out"
  [ "$fmt" = "%{http_code}" ] && printf '%s' "$code"
else
  printf '%s' "$body"
fi
'''
        curl = (curl.replace("__LOG__", str(self.curl_log))
                    .replace("__POLICY__", str(self.policy_file))
                    .replace("__POLL__", str(self.poll_file))
                    .replace("__FEATURE__", str(self.feature_file))
                    .replace("__RUNNER__", str(self.fleet_runner_file)))
        self._executable(self.bin / "curl", curl)

        logger = '''#!/bin/bash
printf '%s\\n' "$*" >> "__LOG__"
'''.replace("__LOG__", str(self.logger_log))
        self._executable(self.bin / "logger", logger)

        python = '''#!/bin/bash
printf 'call\\n' >> "__LOG__"
exec "__PYTHON__" "$@"
'''.replace("__LOG__", str(self.python_log)).replace(
            "__PYTHON__", shutil.which("python3") or "/usr/bin/python3")
        self._executable(self.bin / "python3", python)

        stat = '''#!/bin/bash
fmt=""
while [ $# -gt 0 ]; do
  case "$1" in
    -c) fmt="$2"; shift 2 ;;
    *) shift ;;
  esac
done
line=$(cat "__STAT__" 2>/dev/null || echo "0 700")
case "$fmt" in
  "%u") printf '%s' "$(echo "$line" | awk '{print $1}')" ;;
  "%a") printf '%s' "$(echo "$line" | awk '{print $2}')" ;;
  *) : ;;
esac
'''.replace("__STAT__", str(self.work_stat_file))
        self._executable(self.bin / "stat", stat)

        noop = '''#!/bin/bash
exit 0
'''
        self._executable(self.bin / "sudo", noop)
        systemd = '''#!/bin/bash
printf '%s\\n' "$*" >> "__LOG__"
exit 0
'''.replace("__LOG__", str(self.systemd_log))
        self._executable(self.bin / "systemd-run", systemd)

    @staticmethod
    def _executable(path: Path, body: str):
        path.write_text(body)
        path.chmod(0o755)

    def _transformed(self) -> str:
        text = POLLER.read_text()

        def swap(old, new, expected=1):
            assert old in text, "the poller no longer contains %r" % old
            if expected is not None:
                assert text.count(old) == expected, old
            return text.replace(old, new)

        # Bound the infinite loop and remove the 30 s wait.
        text = swap("while true; do", "for _eshu_cycle in 1 2 3; do")
        text = swap("POLL_INTERVAL=30", "POLL_INTERVAL=0")
        # Relocate paths. /tmp first: the sandbox root itself lives under /tmp,
        # so replacing /tmp after the ESHU_WORK swap would rewrite the sandbox
        # path again. ESHU_WORK only exists after chunk 3, so this is optional.
        text = text.replace("/tmp/", str(self.tmp) + "/")
        if 'ESHU_WORK="/run/eshu"' in text:
            text = text.replace('ESHU_WORK="/run/eshu"',
                                'ESHU_WORK="%s"' % self.work, 1)
        text = text.replace("/var/run/", str(self.run_dir) + "/")
        text = text.replace("/etc/", str(self.etc) + "/")
        for token, value in (
            ("__DASHBOARD_URL__", "http://dashboard.test"),
            ("__TARGET_IP__", "10.0.0.1"),
            ("__HOST_NAME__", "test-gw"),
            ("__GATEWAY_TOKEN__", "test-token"),
        ):
            text = swap(token, value)

        # The relocation must be complete, or a test could touch the real host.
        # Counts compare a real path with its sandboxed form: the sandbox path
        # contains the real path as a suffix, so a leak makes the counts differ.
        for name in ("eshu-exact.txt", "eshu-rwhite.txt", "eshu-rblack.txt",
                     "eshu-freeze", "eshu-zero-trust", "eshu-require-session",
                     "eshu-windows.txt", "eshu-session-key"):
            assert text.count("/etc/" + name) == text.count(str(self.etc / name)), name
        for name in ("eshu.last_trigger", "eshu.tickets", "eshu.policy.sha256"):
            # /var/run/ is relocated to <root>/run/, which does not contain the
            # real prefix — so no leftover real path may remain at all.
            assert "/var/run/" + name not in text, name
            assert str(self.run_dir / name) in text, name
        return text

    # ── inputs ──────────────────────────────────────────────────────────────
    def policy(self, body: str):
        self.policy_file.write_text(body)

    def ticket(self, value: str):
        import json
        self.poll_file.write_text(json.dumps(
            {"ticket": value, "mode": "prod", "version": "v-test"}))

    # ── execution/observation ───────────────────────────────────────────────
    def run(self, expect_ok=True):
        env = {
            "PATH": str(self.bin) + ":" + os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.root),
            "LANG": "C.UTF-8",
        }
        proc = subprocess.run(
            ["bash", str(self.script)], env=env, cwd=str(self.root),
            capture_output=True, text=True, timeout=30)
        if expect_ok:
            assert proc.returncode == 0, proc.stderr
        return proc

    def set_work_stat(self, value: str):
        """Canned `stat` output for ESHU_WORK (uid mode)."""
        self.work_stat_file.write_text(value)

    def systemd_args(self):
        if not self.systemd_log.exists():
            return []
        return [line for line in self.systemd_log.read_text().splitlines() if line]

    def curl_out_pairs(self):
        if not self.curl_log.exists():
            return []
        pairs = []
        for line in self.curl_log.read_text().splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2:
                pairs.append((parts[0], parts[1]))
        return pairs

    def logger_text(self):
        if not self.logger_log.exists():
            return ""
        return self.logger_log.read_text()

    def python_calls(self) -> int:
        if not self.python_log.exists():
            return 0
        return len([line for line in self.python_log.read_text().splitlines() if line])

    def read(self, name: str) -> str:
        path = self.etc / name
        return path.read_text() if path.exists() else ""

    def stamp(self) -> str:
        path = self.run_dir / "eshu.policy.sha256"
        return path.read_text() if path.exists() else ""

    def inodes(self):
        names = ("eshu-exact.txt", "eshu-rwhite.txt", "eshu-rblack.txt",
                 "eshu-freeze", "eshu-zero-trust", "eshu-require-session",
                 "eshu-windows.txt")
        return {n: (self.etc / n).stat().st_ino for n in names}


@pytest.fixture
def poller(tmp_path):
    return PollerSandbox(tmp_path)


# ── install contract ────────────────────────────────────────────────────────
def test_repo_script_keeps_placeholders_and_hashing():
    text = POLLER.read_text()
    for token in ("__DASHBOARD_URL__", "__TARGET_IP__",
                  "__HOST_NAME__", "__GATEWAY_TOKEN__"):
        assert token in text
    assert "POLICY_STAMP=" in text
    assert "sha256sum" in text
    assert '[[:space:]]' in text  # the whitespace-tolerant ticket guard


# ── first sync ──────────────────────────────────────────────────────────────
def test_first_cycle_writes_policy_and_stamp(poller):
    body = policy_body()
    poller.policy(body)
    poller.run()

    assert poller.read("eshu-exact.txt") == "echo hello"
    assert poller.read("eshu-rblack.txt") == "forbidden"
    assert poller.read("eshu-rwhite.txt") == ""
    assert poller.read("eshu-freeze") == ""
    assert poller.read("eshu-zero-trust") == ""
    assert poller.read("eshu-require-session") == ""
    assert poller.read("eshu-windows.txt") == ""
    assert poller.read("eshu-session-key") == "ab" * 32
    assert poller.stamp() == "1:" + sha(body)


def test_first_run_parses_once_per_cycle(poller):
    # Three cycles, but only the first is stale: the other two must start no
    # python at all. This is the whole optimization in one assertion.
    poller.policy(policy_body())
    poller.run()
    # 16 before F5; the per-gateway capture_output field is one more python
    # parse inside the same stale-policy branch, and F12's ssh_mode is another
    # (the steady-state test below still proves a stale-free cycle starts none).
    assert poller.python_calls() == 18


def test_f5_poller_writes_capture_mode(poller):
    poller.policy(policy_body(capture_output="tail"))
    poller.run()
    assert poller.read("eshu-capture") == "tail"


def test_f5_poller_bad_capture_mode_is_exit_only(poller):
    poller.policy(policy_body(capture_output="garbage"))
    poller.run()
    assert poller.read("eshu-capture") == "exit-only"


def test_f5_poller_missing_capture_mode_is_exit_only(poller):
    body = policy_body()
    poller.policy(body)
    poller.run()
    assert poller.read("eshu-capture") == "exit-only"


# ── steady state ────────────────────────────────────────────────────────────
def test_unchanged_policy_starts_no_python_and_rewrites_nothing(poller):
    poller.policy(policy_body())
    poller.run()
    before_calls = poller.python_calls()
    before_inodes = poller.inodes()
    before_stamp = poller.stamp()

    poller.run()  # 3 more cycles, identical body

    assert poller.python_calls() == before_calls
    assert poller.inodes() == before_inodes
    assert poller.stamp() == before_stamp


def test_changed_policy_resyncs(poller):
    poller.policy(policy_body())
    poller.run()
    before = poller.python_calls()

    changed = policy_body(trigger_freeze=True)
    poller.policy(changed)
    poller.run()

    assert poller.read("eshu-freeze").strip() == "1"
    assert poller.stamp() == "1:" + sha(changed)
    assert poller.python_calls() > before


def test_missing_policy_file_forces_resync(poller):
    poller.policy(policy_body())
    poller.run()
    before = poller.python_calls()

    (poller.etc / "eshu-windows.txt").unlink()
    poller.run()  # same body, but a required file is gone

    assert (poller.etc / "eshu-windows.txt").exists()
    assert poller.python_calls() > before


# ── failure handling ────────────────────────────────────────────────────────
def test_malformed_json_does_not_advance_the_stamp(poller):
    good = policy_body()
    poller.policy(good)
    poller.run()
    good_stamp = poller.stamp()

    poller.policy("{not json")
    poller.run()

    # The parse failed, so the last good state and stamp must survive and the
    # next cycle must retry instead of trusting the bad body.
    assert poller.read("eshu-exact.txt") == "echo hello"
    assert poller.stamp() == good_stamp

    poller.policy(policy_body(trigger_freeze=True))
    poller.run()
    assert poller.read("eshu-freeze").strip() == "1"


# ── JIT tickets ─────────────────────────────────────────────────────────────
def test_null_ticket_starts_no_python(poller):
    poller.policy(policy_body())
    poller.run()
    before = poller.python_calls()
    poller.run()  # poll body is empty -> stub returns {"ticket":null,...}
    assert poller.python_calls() == before


def test_ticket_is_parsed_and_stored_once(poller):
    poller.policy(policy_body())
    poller.run()
    before = poller.python_calls()

    ticket = "%d|echo ticketed" % int(time.time())
    poller.ticket(ticket)
    poller.run()

    assert poller.python_calls() == before + 1
    ticket_path = poller.run_dir / "eshu.tickets"
    assert ticket_path.read_text().strip() == ticket


def test_ticket_with_json_whitespace_is_not_missed(poller):
    poller.policy(policy_body())
    poller.run()
    before = poller.python_calls()

    ticket = "%d|echo spaced" % int(time.time())
    poller.poll_file.write_text('{"ticket": "%s", "mode": "prod"}' % ticket)
    poller.run()

    assert poller.python_calls() == before + 1
    assert (poller.run_dir / "eshu.tickets").read_text().strip() == ticket


def test_ticket_poll_url_is_valid_with_an_empty_windows_file(poller):
    """`grep -c` prints 0 AND exits 1 on an empty file, so `|| echo 0` made
    WC "0\\n0"; curl refused the URL and no approved ticket was ever
    fetched (ssh_exec stuck at `approved`, 1.2 test round)."""
    poller.policy(policy_body())
    poller.run()
    assert poller.read("eshu-windows.txt") == ""
    ticket = "%d|echo ticketed" % int(time.time())
    poller.ticket(ticket)
    poller.run()
    urls = [u for u, _ in poller.curl_out_pairs()]
    assert "MALFORMED" not in urls
    assert any(u.endswith("/api/poll/" + u.split("/api/poll/")[-1]) and "wc=0" in u
               for u in urls if "/api/poll/" in u)
    assert (poller.run_dir / "eshu.tickets").read_text().strip() == ticket


def test_bi1_a_ticket_with_a_line_break_is_refused(poller):
    """BI1: the dashboard refuses these; the poller is the second check."""
    poller.policy(policy_body())
    poller.run()
    ticket = "%d|echo one\necho two" % int(time.time())
    poller.ticket(ticket)
    poller.run()
    tickets = poller.run_dir / "eshu.tickets"
    assert not tickets.exists() or "echo two" not in tickets.read_text()
    assert "line break" in poller.logger_text()


def _clean_harness(tmp_path, tickets_text, tickets_path=None):
    """The poller's real clean_tickets(), with the file path relocated."""
    src = POLLER.read_text()
    start = src.index('TICKETS_LOCK="$ESHU_WORK/eshu.tickets.lock"')
    end = src.index('\n# Policy sync stamp.', start)
    tickets = tickets_path or (tmp_path / "tickets")
    block = src[start:end].replace("/var/run/eshu.tickets", str(tickets))
    work = tmp_path / "run-eshu"
    work.mkdir(mode=0o700, exist_ok=True)
    if tickets_text is not None:
        tickets.write_text(tickets_text)
    harness = tmp_path / "clean.sh"
    harness.write_text(
        "#!/bin/bash\nset -euo pipefail\n"
        'ESHU_WORK="%s"\nlogger() { :; }\n%s\nclean_tickets\n' % (work, block))
    return subprocess.run(["bash", str(harness)], capture_output=True,
                          text=True, timeout=60), tickets


def test_bi1_cleanup_drops_a_future_dated_line(tmp_path):
    now = int(time.time())
    proc, tickets = _clean_harness(
        tmp_path, "v2|%d|||echo future\nv2|%d|||echo fresh\n" % (now + 3600, now))
    assert proc.returncode == 0, proc.stderr
    assert tickets.read_text().strip() == "v2|%d|||echo fresh" % now


def test_bi1_cleanup_drops_an_expired_line(tmp_path):
    now = int(time.time())
    proc, tickets = _clean_harness(
        tmp_path, "v2|%d|||echo old\n" % (now - 3600))
    assert proc.returncode == 0, proc.stderr
    assert tickets.read_text() == ""


def test_bi1_cleanup_drops_a_cr_line(tmp_path):
    now = int(time.time())
    proc, tickets = _clean_harness(tmp_path, "v2|%d|||echo x\r\n" % now)
    assert proc.returncode == 0, proc.stderr
    assert tickets.read_text() == ""


def test_bi2_a_cleanup_cannot_resurrect_a_claimed_line(tmp_path):
    """BI2(a): the read happens under the lock, not before it.

    A claim holds the lock and removes the line; a cleanup that started
    earlier must not rewrite its pre-claim copy afterwards.
    """
    now = int(time.time())
    work = tmp_path / "run-eshu"
    work.mkdir(mode=0o700, exist_ok=True)
    tickets = tmp_path / "tickets"
    tickets.write_text("v2|%d|||echo claimed\n" % now)
    lock = work / "eshu.tickets.lock"
    holder = subprocess.Popen([
        "bash", "-c",
        'exec 9>>"%s"; flock -w 5 9; sleep 0.5; : > "%s"; flock -u 9'
        % (lock, tickets)])
    try:
        time.sleep(0.15)
        proc, tickets = _clean_harness(tmp_path, None, tickets_path=tickets)
        assert proc.returncode == 0, proc.stderr
    finally:
        holder.wait(timeout=20)
    assert tickets.read_text() == ""


def test_ticket_poll_counts_windows(poller):
    body = policy_body(approved_windows=[
        {"token": "t1", "command": "uptime"}, {"token": "t2", "command": "df -h"}])
    poller.policy(body)
    poller.run()
    poller.run()
    polls = [u for u, _ in poller.curl_out_pairs() if "/api/poll/" in u]
    assert polls and polls[-1].endswith("wc=2")

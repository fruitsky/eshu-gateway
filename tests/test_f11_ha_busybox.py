"""F11 part 2 — the six fixed scripts really run under BusyBox `ash`.

The transport builds one `sh -c` string per step and sends it to the HA SSH
add-on, whose shell is BusyBox ash. Part 1's tests checked the script text; this
file executes it under a real BusyBox (`busybox sh -c`) against a throwaway
`/homeassistant` tree, with `ha` stubbed to pass or fail.

The add-on's root is `/homeassistant`; here the scripts' literal is rewritten to
the temp tree, exactly like the fleet-runner harness rewrites `/etc/eshu-token`.
The test skips (with a reason) where BusyBox is not installed.
"""
import hashlib
import os
import shlex
import shutil
import stat
import subprocess

import pytest

from core import ha_config_ssh as ha

BUSYBOX = shutil.which("busybox")

pytestmark = pytest.mark.skipif(
    not BUSYBOX,
    reason="BusyBox is not installed; run these under ash where it is "
           "(apt install busybox), the add-on's shell",
)

# Every applet the six scripts call. BusyBox provides all of them; linking them
# from the busybox binary makes the test exercise the BusyBox versions even on
# a host whose PATH also has GNU tools.
APPLETS = ("realpath", "stat", "sha256sum", "cut", "cp", "mv", "chmod",
           "chown", "basename", "dirname", "find", "cat", "head", "timeout",
           "date")


def sha(data):
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


class BusyBox:
    """A temp config tree, BusyBox applets, and a stub `ha`."""

    def __init__(self, tmp_path):
        self.root = (tmp_path / "homeassistant").resolve()
        (self.root / "packages").mkdir(parents=True)
        self.tmp_path = tmp_path
        self.bindir = tmp_path / "bin"
        self.bindir.mkdir()
        for applet in APPLETS:
            link = self.bindir / applet
            if not link.exists():
                link.symlink_to(BUSYBOX)
        self.ha_exit = tmp_path / "ha-exit"
        self.ha_exit.write_text("0")
        self.ha_log = tmp_path / "ha.log"
        stub = self.bindir / "ha"
        stub.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >> \"__LOG__\"\n"
            "exit \"$(cat \"__EXIT__\")\"\n"
            .replace("__LOG__", str(self.ha_log))
            .replace("__EXIT__", str(self.ha_exit)))
        stub.chmod(0o755)
        self.env = {
            "PATH": "%s:/usr/bin:/bin" % self.bindir,
            "HOME": str(tmp_path),
            "TMPDIR": str(tmp_path),
            "LANG": "C.UTF-8",
        }

    def ha(self, code):
        self.ha_exit.write_text(str(code))

    def run(self, script, *args, stdin=None, rewrite=True):
        if rewrite:
            script = script.replace("/homeassistant", str(self.root))
        cmd = [BUSYBOX, "sh", "-c", script, "_"] + [str(a) for a in args]
        return subprocess.run(cmd, input=stdin, capture_output=True, text=True,
                              env=self.env, timeout=60)

    def file(self, name, content, mode=0o640):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode() if isinstance(content, str) else content)
        os.chmod(path, mode)
        return path

    def write_args(self, path, old_content):
        backup = "%s.eshu-1700000000.bak" % path
        tmp = "%s.eshu-tmp-0123456789abcdef" % path
        return [path, sha(old_content), backup, tmp], backup, tmp

    def backup(self, path, content):
        backup = "%s.eshu-1700000000.bak" % path
        shutil.copy2(path, backup)   # the real backup is `cp -p`
        with open(backup, "wb") as fh:
            fh.write(content.encode() if isinstance(content, str) else content)
        return backup


@pytest.fixture
def box(tmp_path):
    return BusyBox(tmp_path)


# ── the list script ───────────────────────────────────────────────────────

def test_the_list_script_finds_yaml_at_the_root_and_in_packages(box):
    one = box.file("a.yaml", "a: 1\n")
    two = box.file("packages/b.yml", "b: 2\n")
    box.file("notes.txt", "not yaml\n")
    proc = box.run(ha.LIST_SCRIPT)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert str(one) in out and str(two) in out
    assert "notes.txt" not in out
    # BusyBox `stat -c '%s %Y %n'` gives size, mtime and path.
    line = [l for l in out.splitlines() if str(one) in l][0].split(None, 2)
    assert int(line[0]) == len("a: 1\n") and int(line[1]) > 0


# ── the write script ──────────────────────────────────────────────────────

def test_the_write_script_succeeds_and_keeps_mode_and_owner(box):
    path = box.file("configuration.yaml", "old: 1\n", mode=0o640)
    before = os.stat(path)
    args, backup, tmp = box.write_args(path, "old: 1\n")
    proc = box.run(ha.WRITE_SCRIPT, *args, stdin="new: 2\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert path.read_text() == "new: 2\n"
    after = os.stat(path)
    assert stat.S_IMODE(after.st_mode) == 0o640
    assert (after.st_uid, after.st_gid) == (before.st_uid, before.st_gid)
    assert open(backup, "rb").read() == b"old: 1\n"
    assert not os.path.exists(tmp)
    assert proc.stdout.startswith("ok " + sha("new: 2\n"))
    assert box.ha_log.read_text().strip().endswith("core check")


def test_the_write_script_rolls_back_bytes_mode_and_owner_on_a_failed_check(box):
    path = box.file("packages/x.yaml", "keep: me\n", mode=0o600)
    before = os.stat(path)
    box.ha(1)
    args, backup, _tmp = box.write_args(path, "keep: me\n")
    proc = box.run(ha.WRITE_SCRIPT, *args, stdin="broken: [\n")
    assert proc.returncode == 18, proc.stdout + proc.stderr
    assert path.read_bytes() == b"keep: me\n"
    after = os.stat(path)
    assert stat.S_IMODE(after.st_mode) == 0o600
    assert (after.st_uid, after.st_gid) == (before.st_uid, before.st_gid)
    assert os.path.exists(backup)  # the operator can inspect/restore it


def test_the_write_script_refuses_a_path_outside_the_tree(box):
    outside = box.tmp_path / "outside.yaml"
    outside.write_text("x: 1\n")
    args = [outside, sha("x: 1\n"), str(outside) + ".eshu-1700000000.bak",
            str(outside) + ".eshu-tmp-0123456789abcdef"]
    proc = box.run(ha.WRITE_SCRIPT, *args, stdin="x: 2\n")
    assert proc.returncode == 11
    assert outside.read_text() == "x: 1\n"


def test_the_write_script_refuses_a_symlinked_target(box):
    real = box.file("real.yaml", "r: 1\n")
    link = box.root / "link.yaml"
    link.symlink_to(real)
    args = [link, sha("r: 1\n"), str(real) + ".eshu-1700000000.bak",
            str(real) + ".eshu-tmp-0123456789abcdef"]
    proc = box.run(ha.WRITE_SCRIPT, *args, stdin="r: 2\n")
    # The realpath's basename differs from the requested one.
    assert proc.returncode == 13
    assert real.read_text() == "r: 1\n"


def test_the_write_script_refuses_a_symlinked_directory(box):
    outside = box.tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "y.yaml").write_text("y: 1\n")
    (box.root / "out").symlink_to(outside)
    path = box.root / "out" / "y.yaml"
    args = [path, sha("y: 1\n"), str(path) + ".eshu-1700000000.bak",
            str(path) + ".eshu-tmp-0123456789abcdef"]
    proc = box.run(ha.WRITE_SCRIPT, *args, stdin="y: 2\n")
    assert proc.returncode == 11
    assert (outside / "y.yaml").read_text() == "y: 1\n"


def test_the_write_script_writes_crlf_bytes_from_stdin(box):
    """AT2: a CRLF payload is committed byte-for-byte, never translated."""
    path = box.file("automations.yaml", "a: 1\r\n")
    payload = "a: 2\r\nb: 3\r\n"
    args, _backup, _tmp = box.write_args(path, "a: 1\r\n")
    proc = box.run(ha.WRITE_SCRIPT, *args, stdin=payload)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert path.read_bytes() == payload.encode()
    assert proc.stdout.strip() == "ok " + sha(payload)


# ── the restore script ────────────────────────────────────────────────────

def test_the_restore_script_puts_the_backup_back_and_rechecks(box):
    path = box.file("configuration.yaml", "new: 2\n", mode=0o640)
    backup = box.backup(path, "old: 1\n")
    tmp = "%s.eshu-tmp-0123456789abcdef" % path
    proc = box.run(ha.RESTORE_SCRIPT, path, backup, tmp)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert path.read_text() == "old: 1\n"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o640
    assert not os.path.exists(tmp)
    assert box.ha_log.read_text().strip().endswith("core check")


def test_the_restore_script_puts_the_current_bytes_back_on_a_failed_check(box):
    path = box.file("configuration.yaml", "current: 2\n")
    backup = box.backup(path, "old: 1\n")
    box.ha(1)
    tmp = "%s.eshu-tmp-0123456789abcdef" % path
    proc = box.run(ha.RESTORE_SCRIPT, path, backup, tmp)
    assert proc.returncode == 18, proc.stdout + proc.stderr
    assert path.read_text() == "current: 2\n"
    assert not os.path.exists(tmp)


# ── the three one-line commands ───────────────────────────────────────────

def test_the_verify_command_runs_under_busybox(box):
    path = box.file("configuration.yaml", "a: 1\n")
    quoted = shlex.quote(str(path))
    proc = box.run("realpath %s && stat -c '%%F' -- %s" % (quoted, quoted),
                   rewrite=False)
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert lines[0] == str(path)
    assert "regular file" in lines[1].lower()


def test_the_read_command_runs_under_busybox(box):
    path = box.file("configuration.yaml", "a: 1\n")
    proc = box.run("head -c %d -- %s" % (ha.MAX_CONTENT + 1,
                                         shlex.quote(str(path))),
                   rewrite=False)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "a: 1\n"


def test_the_check_command_runs_under_busybox(box):
    proc = box.run("timeout 100 ha core check 2>&1")
    assert proc.returncode == 0
    box.ha(1)
    proc = box.run("timeout 100 ha core check 2>&1")
    assert proc.returncode == 1

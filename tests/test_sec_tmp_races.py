"""Chunk 3: root must never read/execute a fixed path in a world-writable dir.

The poller runs as root, so its downloads and scratch files live in a private
root-owned 0700 work dir instead of fixed /tmp names. The fleet runner gets an
output path in that dir, the installer uses mktemp for its syntax log, and the
enrollment bootstrap downloads into a private mktemp -d.
"""
import shutil
from pathlib import Path

import pytest

from test_poller import PollerSandbox, policy_body

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def poller(tmp_path):
    return PollerSandbox(tmp_path)


# ── poller work dir ────────────────────────────────────────────────────────
def test_sec_chunk3_poller_writes_only_under_work_dir(poller):
    poller.policy(policy_body())
    poller.run()

    # Nothing may land in the fake /tmp, and every curl -o target except the
    # feature script (which lives in /etc/eshu/features.d) is under ESHU_WORK.
    assert list(poller.tmp.iterdir()) == []
    outs = [(url, out) for url, out in poller.curl_out_pairs() if out]
    assert outs
    for url, out in outs:
        if "/static/features/" in url:
            continue
        assert out.startswith(str(poller.work)), (url, out)


def test_sec_chunk3_refuses_symlink_work_dir(poller):
    target = poller.root / "elsewhere"
    target.mkdir()
    shutil.rmtree(poller.work)
    poller.work.symlink_to(target, target_is_directory=True)

    proc = poller.run(expect_ok=False)
    assert proc.returncode != 0
    text = poller.logger_text()
    assert "ESHU_WORK" in text
    assert "symlink" in text or "refusing" in text


@pytest.mark.parametrize("stat_value", ["0 777", "0 755", "1000 700"])
def test_sec_chunk3_refuses_unsafe_work_dir(poller, stat_value):
    poller.set_work_stat(stat_value)

    proc = poller.run(expect_ok=False)
    assert proc.returncode != 0
    text = poller.logger_text()
    assert "ESHU_WORK" in text
    assert "0700" in text or "refusing" in text


def test_sec_chunk3_umask_is_restrictive():
    import re
    text = (REPO / "dashboard" / "eshu-poller.sh").read_text()
    assert re.search(r"(?m)^umask 077$", text)


# ── no fixed world-writable paths ──────────────────────────────────────────
@pytest.mark.parametrize("rel", [
    "dashboard/eshu-poller.sh",
    "dashboard/static/fleet-runner.sh",
    "dashboard/eshu-installer-template.sh",
])
def test_sec_chunk3_no_fixed_tmp_paths(rel):
    text = (REPO / rel).read_text()
    assert "/tmp/" not in text


def test_sec_chunk3_installer_syntax_log_uses_mktemp():
    text = (REPO / "dashboard" / "eshu-installer-template.sh").read_text()
    assert "/tmp/eshu-syntax-error.log" not in text
    assert "mktemp" in text


# ── fleet runner output path ───────────────────────────────────────────────
def test_sec_chunk3_fleet_output_path_is_under_work_dir(poller):
    poller.policy(policy_body(
        pending_fleet_cmd_id="7",
        pending_fleet_cmd="echo fleet",
        pending_fleet_cmd_timeout="30",
    ))
    poller.run()

    args = poller.systemd_args()
    assert args, "poller never launched the fleet runner"
    launched = args[-1]
    assert str(poller.work) in launched
    assert "eshu-fleet-out-7.txt" in launched
    assert "eshu-fleet-7.sh" in launched


# ── enrollment bootstrap ───────────────────────────────────────────────────
def test_sec_chunk3_enroll_script_uses_private_tempdir(client, enrollment_token):
    from database import save_ssh_keys
    save_ssh_keys("ssh-ed25519 AAAAtestkey test@example")

    r = client.get(f"/api/enroll?token={enrollment_token}")
    assert r.status_code == 200
    text = r.text
    assert "mktemp -d" in text
    assert 'trap \'rm -rf "$T"\' EXIT' in text
    assert "/tmp/eshu-install.sh" not in text
    assert '"$T/eshu-install.sh"' in text

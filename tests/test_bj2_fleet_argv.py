"""BJ2 — the Fleet Run runner keeps the gateway token out of argv.

The poller starts the runner through `systemd-run`, whose ExecStart any local
user can read with `systemctl show` for as long as the unit exists; the runner
then started python3 with the token as an argument. The runner now reads
/etc/eshu-token itself (root, 0600) and passes it to python in the environment.
"""
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "dashboard" / "static" / "fleet-runner.sh"


def _harness(tmp_path, token="tok-abc123"):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    seen = tmp_path / "python-calls"
    stub = bindir / "python3"
    body = ('#!/bin/sh\n'
            'printf "ARGV:%s\\n" "$*" >> "__SEEN__"\n'
            'printf "ENV:%s\\n" "${ESHU_FLEET_TOKEN:-}" >> "__SEEN__"\n'
            "cat >/dev/null\n").replace("__SEEN__", str(seen))
    stub.write_text(body)
    stub.chmod(0o755)
    token_file = tmp_path / "eshu-token"
    token_file.write_text(token + "\n")
    script = tmp_path / "runner.sh"
    script.write_text(RUNNER.read_text().replace("/etc/eshu-token", str(token_file)))
    cmd = tmp_path / "cmd.sh"
    cmd.write_text("#!/bin/bash\necho fleet-ok\n")
    out = tmp_path / "out.txt"
    return script, bindir, seen, cmd, out, token


def _run(script, bindir, tmp_path, *args):
    env = {"PATH": "%s:%s" % (bindir, os.environ.get("PATH", "/usr/bin:/bin")),
           "HOME": str(tmp_path)}
    return subprocess.run(["bash", str(script), *args], env=env,
                          capture_output=True, text=True, timeout=60)


def test_bj2_the_runner_never_passes_the_token_to_python(tmp_path):
    script, bindir, seen, cmd, out, token = _harness(tmp_path)
    proc = _run(script, bindir, tmp_path, "http://dash.test", "192.0.2.10",
                "7", "5", str(cmd), str(out))
    assert proc.returncode == 0, proc.stderr
    text = seen.read_text()
    argv_lines = [l for l in text.splitlines() if l.startswith("ARGV:")]
    env_lines = [l for l in text.splitlines() if l.startswith("ENV:")]
    assert argv_lines
    assert token not in " ".join(argv_lines)
    # The token does reach python, through the environment.
    assert all(line == "ENV:" + token for line in env_lines), env_lines
    assert "192.0.2.10" in argv_lines[0] and "7" in argv_lines[0]


def test_bj2_the_old_poller_argument_shape_still_runs(tmp_path):
    """Old pollers pass the token as $2 until the installer updates them."""
    script, bindir, seen, cmd, out, token = _harness(tmp_path)
    proc = _run(script, bindir, tmp_path, "http://dash.test", token,
                "192.0.2.10", "8", "5", str(cmd), str(out))
    assert proc.returncode == 0, proc.stderr
    text = seen.read_text()
    argv_lines = [l for l in text.splitlines() if l.startswith("ARGV:")]
    assert argv_lines and token not in " ".join(argv_lines)
    assert "192.0.2.10" in argv_lines[0]


def test_bj2_the_runner_refuses_without_a_token_file(tmp_path):
    script, bindir, seen, cmd, out, _token = _harness(tmp_path)
    (tmp_path / "eshu-token").unlink()
    proc = _run(script, bindir, tmp_path, "http://dash.test", "192.0.2.10",
                "7", "5", str(cmd), str(out))
    assert proc.returncode != 0
    assert "refusing" in proc.stderr.lower()


def test_bj2_no_script_puts_the_gateway_token_in_fleet_argv():
    text = RUNNER.read_text()
    # The token is not an argument and not a python argv element.
    assert 'GATEWAY_TOKEN="$2"' not in text
    assert '"$GATEWAY_TOKEN" "$TARGET_IP"' not in text
    assert 'ESHU_FLEET_TOKEN="$GATEWAY_TOKEN" python3' in text
    poller = (REPO / "dashboard" / "eshu-poller.sh").read_text()
    unit = poller[poller.index('systemd-run --unit="eshu-fleet'):]
    unit = unit[:unit.index("; then")]
    assert "GATEWAY_TOKEN" not in unit

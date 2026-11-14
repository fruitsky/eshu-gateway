"""Per-view browser check: no horizontal scroll, the view renders, text meets AA.

The harness is `tests/ui/ui_views.cjs`; it needs a real dashboard because the
list layouts it measures come from the fetched data. `tests/ui/serve_ui.py`
serves one on a throwaway seeded database (synthetic 192.0.2.x data only).

It skips, with a reason, where node or playwright is unavailable — a developer
machine. CI installs both (see .github/workflows/ci.yml), so the check runs
there.
"""
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "ui" / "ui_views.cjs"
PASSWORD = "synthetic-ui-pass"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture()
def ui_server(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed, so the browser check cannot run")
    env = dict(os.environ)
    env["ESHU_UI_DB"] = str(tmp_path / "ui.db")
    env["PYTHONPATH"] = str(ROOT / "dashboard")
    seeded = subprocess.run([sys.executable, str(ROOT / "tests" / "ui" / "seed_ui.py")],
                            env=env, capture_output=True, text=True, timeout=120)
    assert seeded.returncode == 0, seeded.stderr[-2000:]

    port = _free_port()
    env["ESHU_UI_PORT"] = str(port)
    server = subprocess.Popen([sys.executable, str(ROOT / "tests" / "ui" / "serve_ui.py")],
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True)
    base = "http://127.0.0.1:%d/" % port
    try:
        for _ in range(80):
            if server.poll() is not None:
                raise AssertionError("ui server exited:\n" + (server.stderr.read() or "")[-2000:])
            try:
                with urllib.request.urlopen(base + "api/version", timeout=1) as res:
                    if res.status == 200:
                        break
            except OSError:
                time.sleep(0.25)
        else:
            raise AssertionError("ui server did not answer on " + base)
        yield base
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


def test_every_view_fits_and_meets_aa(ui_server):
    env = dict(os.environ)
    env["ESHU_UI_URL"] = ui_server
    env["ESHU_UI_PASSWORD"] = PASSWORD
    proc = subprocess.run([shutil.which("node"), str(HARNESS)], env=env,
                          capture_output=True, text=True, timeout=180)
    if proc.returncode == 2:
        pytest.skip("headless Chromium not available: " + proc.stderr.strip()[:200])
    assert proc.returncode == 0, proc.stderr[-4000:]
    assert "ui_views: ok" in proc.stdout

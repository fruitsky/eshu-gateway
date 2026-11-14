import tempfile
import os
import sys

import pytest

_dashboard_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dashboard")
sys.path.insert(0, _dashboard_dir)


@pytest.fixture(autouse=True)
def temp_db():
    import db.core as db_core
    import core.rate_limit
    import core.web_fetch_limit
    from core.agent_context import reset_session_memory, set_current_agent
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    old_path = db_core.DB_PATH
    db_core.DB_PATH = path
    from db.core import init_db
    init_db()
    core.rate_limit._rate_limit_buckets.clear()
    core.web_fetch_limit.reset()
    set_current_agent(None)
    reset_session_memory()
    yield
    core.rate_limit._rate_limit_buckets.clear()
    core.web_fetch_limit.reset()
    set_current_agent(None)
    reset_session_memory()
    db_core.DB_PATH = old_path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture(autouse=True)
def installer_channels_dir(tmp_path, monkeypatch):
    """X7: each test gets its own Edge/Fleet channel directory, so no test
    writes into dashboard/installer-channels/ in the checkout."""
    path = tmp_path / "installer-channels"
    monkeypatch.setenv("ESHU_INSTALLER_CHANNELS", str(path))
    # Pre-X7 copies in the checkout must never seed a test's channels.
    from core import installer_channels
    missing = str(tmp_path / "no-legacy")
    monkeypatch.setattr(installer_channels, "_legacy_paths", lambda: {
        installer_channels.FLEET: missing, installer_channels.EDGE: missing,
        installer_channels.FLEET_PREV: missing})
    return path


@pytest.fixture
def client(temp_db):
    from db.auth import set_password_hash
    from core.utils import _hash_password
    set_password_hash(_hash_password("test"))
    os.makedirs(os.path.join(_dashboard_dir, "static"), exist_ok=True)
    from main import app
    from fastapi.testclient import TestClient
    return TestClient(app)


@pytest.fixture
def auth_client(client):
    # Chunk 4: session-cookie writes need the CSRF header (or a same Origin).
    client.headers.update({"X-Eshu-CSRF": "1"})
    client.post("/api/auth/login", json={"password": "test"})
    return client


@pytest.fixture
def gateway_headers(temp_db):
    """Seed gateway 10.0.0.1 with a token directly and return its auth header.

    (No longer obtained via an unauthenticated POST /api/register, which now
    requires either the gateway token or an enrollment token.)"""
    from db.gateways import register_gateway, set_gateway_token
    register_gateway("10.0.0.1", "test-gateway", "v15.3")
    set_gateway_token("10.0.0.1", "testtoken-10-0-0-1")
    return {"X-Gateway-Token": "testtoken-10-0-0-1"}


@pytest.fixture
def enrollment_token(temp_db):
    """A valid, single-use enrollment token for /api/register."""
    from db.enrollment import generate_enrollment_token
    return generate_enrollment_token()


@pytest.fixture
def mock_upstream():
    """A tiny threaded HTTP server that echoes back the method/path/auth header
    of each request, used as the upstream for integration-proxy tests."""
    import threading
    import http.server
    import json

    class _Handler(http.server.BaseHTTPRequestHandler):
        requests = []

        def _respond(self, method):
            length = int(self.headers.get('Content-Length', 0) or 0)
            body = self.rfile.read(length) if length else b''
            rec = {
                'method': method,
                'path': self.path,
                'authorization': self.headers.get('Authorization', ''),
                'body': body.decode('utf-8', 'replace'),
            }
            type(self).requests.append(rec)
            payload = json.dumps({'ok': True, 'request': rec}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            self._respond('GET')

        def do_POST(self):
            self._respond('POST')

        def do_PUT(self):
            self._respond('PUT')

        def do_DELETE(self):
            self._respond('DELETE')

        def log_message(self, *args):
            pass

    _Handler.requests = []
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    yield {'base_url': base_url, 'handler': _Handler, 'requests': _Handler.requests}
    server.shutdown()
    server.server_close()


# ── CI failure reporting ────────────────────────────────────────────────────
# GitHub renders `::error` workflow commands as check-run annotations, and the
# annotations are readable through the API without repo-admin rights (the job
# log is not). That is the difference that made a CI-only failure impossible to
# diagnose from outside: `bash scripts/verify.sh` says which test failed, the
# workflow log is private. With this hook a failing CI run names the tests and
# the first line of each failure, so "it fails in CI but not locally" can be
# answered without asking an admin for a log download.
#
# Harmless locally: `GITHUB_ACTIONS` is only set on Actions runners.
_CI_FAILED = []


def _ci_escape(text: str) -> str:
    return (str(text).replace('%', '%25').replace('\r', '%0D')
            .replace('\n', '%0A'))


def pytest_runtest_logreport(report):
    import os
    import sys
    if not os.environ.get('GITHUB_ACTIONS') or not report.failed:
        return
    nodeid = getattr(report, 'nodeid', '?')
    if nodeid in _CI_FAILED:
        return
    _CI_FAILED.append(nodeid)
    if len(_CI_FAILED) > 40:
        return
    detail = getattr(report, 'longreprtext', '') or ''
    lines = [line for line in detail.splitlines() if line.strip()]
    # The first line is `self = <…>`; the `E ` lines carry the assertion.
    tail = [line for line in lines if line.startswith('E ')] or lines[-6:]
    sys.stdout.write('\n::error file=%s,line=1,title=%s::%s\n'
                     % (_ci_escape(nodeid.split('::')[0]),
                        _ci_escape(nodeid)[:200],
                        _ci_escape(' | '.join(tail))[:900]))
    sys.stdout.flush()


def pytest_sessionfinish(session, exitstatus):
    import os
    import sys
    if not os.environ.get('GITHUB_ACTIONS'):
        return
    total = len(getattr(session, 'items', []) or [])
    sys.stdout.write('\n::notice::%d failed of %d collected%s\n'
                     % (len(_CI_FAILED), total,
                        (' — ' + ', '.join(_CI_FAILED[:10])) if _CI_FAILED else ''))
    sys.stdout.flush()

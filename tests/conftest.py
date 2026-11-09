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


# ── Temporary CI diagnostics ────────────────────────────────────────────────
# GitHub Actions parses `::error::` lines from the job log into check-run
# annotations. The job log itself needs admin rights to download, but the
# annotations are public, so this makes a CI-only failure readable from
# outside. Harmless locally (the env var is only set on Actions).
_DIAG_FAILURES = []


def pytest_runtest_logreport(report):
    import os
    import sys
    if not os.environ.get('GITHUB_ACTIONS'):
        return
    if report.failed and report.when in ('setup', 'call'):
        nodeid = getattr(report, 'nodeid', '?')
        if nodeid in _DIAG_FAILURES:
            return
        _DIAG_FAILURES.append(nodeid)
        if len(_DIAG_FAILURES) > 40:
            return
        detail = (report.longreprtext or '').replace('\n', ' | ')[:600]
        sys.stdout.write('\n::error title=%s::%s\n' % (nodeid, detail))
        sys.stdout.flush()

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
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    old_path = db_core.DB_PATH
    db_core.DB_PATH = path
    from db.core import init_db
    init_db()
    core.rate_limit._rate_limit_buckets.clear()
    yield
    core.rate_limit._rate_limit_buckets.clear()
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
    client.post("/api/auth/login", json={"password": "test"})
    return client


@pytest.fixture
def gateway_headers(client):
    r = client.post("/api/register", json={
        "ip": "10.0.0.1",
        "hostname": "test-gateway",
        "version": "v15.3"
    })
    token = r.json()["gateway_token"]
    return {"X-Gateway-Token": token}


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

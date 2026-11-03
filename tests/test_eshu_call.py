"""Tests for the generic Eshu REST client (no external network)."""
import http.server
import json
import os
import sys
import tempfile
import threading

import pytest

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
sys.path.insert(0, SCRIPTS)
import eshu_call  # noqa: E402


class _Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, status, payload, content_type="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/ok"):
            self._send(200, {"send": True, "text": "hello"})
        elif self.path.endswith("/silent"):
            self._send(200, {"send": False, "text": "nope"})
        elif self.path.endswith("/raw"):
            self._send(200, b"plain text\n", "text/plain")
        else:
            self._send(404, {"message": "missing"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        self._send(200, {
            "send": True,
            "text": "posted",
            "auth": self.headers.get("Authorization", ""),
            "body": body.decode("utf-8", "replace"),
        })

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def test_print_text_only_when_send(server, capsys):
    rc = eshu_call.main([
        "--url", server, "--path", "/api/ok", "--token", "t",
        "--print-field", "text", "--when-field", "send",
    ])
    assert rc == 0
    assert capsys.readouterr().out == "hello\n"


def test_silent_when_not_send(server, capsys):
    rc = eshu_call.main([
        "--url", server, "--path", "/api/silent", "--token", "t",
        "--print-field", "text", "--when-field", "send",
    ])
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_http_error_to_stderr(server, capsys):
    rc = eshu_call.main([
        "--url", server, "--path", "/api/error", "--token", "t",
    ])
    assert rc == 3
    assert "missing" in capsys.readouterr().err


def test_post_body_and_auth(server, capsys):
    rc = eshu_call.main([
        "--url", server, "--path", "/api/post", "--token", "secret",
        "--body", '{"person": "gui"}', "--json",
    ])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["auth"] == "Bearer secret"
    assert data["body"] == '{"person": "gui"}'


def test_raw_response(server, capsys):
    rc = eshu_call.main([
        "--url", server, "--path", "/api/raw", "--token", "t", "--raw",
    ])
    assert rc == 0
    assert capsys.readouterr().out == "plain text\n"


def test_token_file(server, capsys):
    fd, path = tempfile.mkstemp()
    os.close(fd)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("file-token\n")
        os.chmod(path, 0o600)
        rc = eshu_call.main([
            "--url", server, "--path", "/api/ok", "--token-file", path,
            "--print-field", "text",
        ])
        assert rc == 0
        assert capsys.readouterr().out == "hello\n"
    finally:
        os.unlink(path)


def test_client_artifact_requires_token(client):
    response = client.get("/api/clients/eshu-call.py")
    assert response.status_code == 401


def test_client_artifact_served_with_version_and_hash(client):
    from db.agent_tokens import create_agent_token
    raw, _agent_id = create_agent_token("artifact-test")
    response = client.get(
        "/api/clients/eshu-call.py",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200
    assert "def main(" in response.text
    assert response.headers["X-Client-Version"] == "1.0.0"
    assert len(response.headers["X-Client-SHA256"]) == 64


def test_rejects_absolute_path(server):
    rc = eshu_call.main([
        "--url", server, "--path", "http://evil.example/x", "--token", "t",
    ])
    assert rc == 2

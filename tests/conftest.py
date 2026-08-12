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

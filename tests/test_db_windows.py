def test_create_approved_window():
    from db.windows import create_approved_window, get_approved_windows
    result = create_approved_window("10.0.0.1", "uptime", 0, 0, 1, "test", 0, 0)
    assert "token" in result
    assert len(result["token"]) > 0
    wins = get_approved_windows()
    assert len(wins) == 1
    assert wins[0]["token"] == result["token"]
    assert wins[0]["command"] == "uptime"
    assert wins[0]["target_ip"] == "10.0.0.1"


def test_create_approved_window_unique_tokens():
    from db.windows import create_approved_window
    r1 = create_approved_window("10.0.0.1", "cmd-a", 0, 0, 1, "a", 0, 0)
    r2 = create_approved_window("10.0.0.1", "cmd-b", 0, 0, 1, "b", 0, 0)
    assert r1["token"] != r2["token"]


def test_increment_window_execution():
    from db.windows import create_approved_window, increment_window_execution, get_approved_windows
    result = create_approved_window("10.0.0.1", "uptime", 0, 0, 1, "test", 0, 0)
    assert increment_window_execution(result["token"]) is True
    wins = get_approved_windows()
    assert wins[0]["execution_count"] == 1


def test_increment_window_execution_rejects_expired():
    import time
    from db.windows import create_approved_window, increment_window_execution, get_approved_windows
    past = int(time.time()) - 3600
    result = create_approved_window("10.0.0.1", "uptime", 0, 0, 1, "test", 0, 0, expires_at=past)
    assert increment_window_execution(result["token"]) is False
    wins = get_approved_windows()
    assert wins[0]["execution_count"] == 0


def test_increment_window_execution_allows_unexpired():
    import time
    from db.windows import create_approved_window, increment_window_execution, get_approved_windows
    future = int(time.time()) + 86400
    result = create_approved_window("10.0.0.1", "uptime", 0, 0, 1, "test", 0, 0, expires_at=future)
    assert increment_window_execution(result["token"]) is True
    wins = get_approved_windows()
    assert wins[0]["execution_count"] == 1


def test_get_approved_window_by_id():
    from db.windows import create_approved_window, get_approved_window_by_id
    result = create_approved_window("10.0.0.1", "uptime", 0, 0, 1, "test", 0, 0)
    win = get_approved_window_by_id(result["id"])
    assert win is not None
    assert win["command"] == "uptime"
    assert win["id"] == result["id"]


def test_delete_approved_window():
    from db.windows import create_approved_window, delete_approved_window, get_approved_windows
    result = create_approved_window("10.0.0.1", "deleteme", 0, 0, 1, "x", 0, 0)
    assert len(get_approved_windows()) == 1
    delete_approved_window(result["id"])
    assert len(get_approved_windows()) == 0


def test_toggle_approved_window():
    from db.windows import create_approved_window, toggle_approved_window, get_approved_windows
    result = create_approved_window("10.0.0.1", "toggler", 0, 0, 1, "x", 0, 0)
    assert get_approved_windows()[0]["enabled"] == 1
    toggle_approved_window(result["id"], False)
    assert get_approved_windows()[0]["enabled"] == 0
    toggle_approved_window(result["id"], True)
    assert get_approved_windows()[0]["enabled"] == 1


def test_create_window_request():
    from db.windows import create_window_request, get_pending_window_requests
    result = create_window_request("10.0.0.1", "uptime", 0, 0, None, "exact", 1, "req-test", 0)
    assert "id" in result
    peding = get_pending_window_requests()
    assert len(peding) == 1
    assert peding[0]["command"] == "uptime"


def test_record_window_execution():
    from db.windows import create_approved_window, record_window_execution, get_window_executions
    result = create_approved_window("10.0.0.1", "uptime", 0, 0, 1, "x", 0, 0)
    record_window_execution(result["id"], result["token"], "10.0.0.1", "uptime")
    execs = get_window_executions(result["id"])
    assert len(execs) == 1
    assert execs[0]["command"] == "uptime"


def test_get_active_excludes_expired():
    import time
    from db.windows import create_approved_window, get_active_approved_windows
    # Window without expiry — should be active
    r1 = create_approved_window("10.0.0.1", "uptime", 0, 0, 1, "x", 0, 0, expires_at=None)
    # Window with future expiry — should be active
    future = int(time.time()) + 86400
    r2 = create_approved_window("10.0.0.1", "hostname", 0, 0, 1, "x", 0, 0, expires_at=future)
    # Window with past expiry — should be excluded
    past = int(time.time()) - 3600
    r3 = create_approved_window("10.0.0.1", "date", 0, 0, 1, "x", 0, 0, expires_at=past)
    active = get_active_approved_windows("10.0.0.1")
    tokens = {w["token"] for w in active}
    assert r1["token"] in tokens
    assert r2["token"] in tokens
    assert r3["token"] not in tokens

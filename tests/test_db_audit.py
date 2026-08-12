def test_record_and_get_audit_log():
    from db.audit import record_audit_event, get_audit_log
    record_audit_event("test_event", "10.0.0.1", "test-host", "test details")
    logs = get_audit_log()
    assert len(logs) == 1
    assert logs[0]["event_type"] == "test_event"
    assert logs[0]["gateway_ip"] == "10.0.0.1"
    assert logs[0]["hostname"] == "test-host"
    assert logs[0]["details"] == "test details"


def test_search_audit_log():
    from db.audit import record_audit_event, search_audit_log
    record_audit_event("jit_created", "10.0.0.1", details="JIT uptime")
    record_audit_event("jit_approved", details="approved by operator")
    record_audit_event("window_created", "10.0.0.2", details="window for cleanup")
    results = search_audit_log("jit")
    assert len(results) == 2
    results = search_audit_log("approved by")
    assert len(results) == 1
    results = search_audit_log("cleanup")
    assert len(results) == 1


def test_audit_log_ordering():
    import time
    from db.audit import record_audit_event, get_audit_log
    record_audit_event("first", details="oldest")
    time.sleep(1.1)
    record_audit_event("second", details="newest")
    logs = get_audit_log()
    assert len(logs) == 2
    assert logs[0]["details"] == "newest"
    assert logs[1]["details"] == "oldest"


def test_audit_log_limit():
    from db.audit import record_audit_event, get_audit_log
    for i in range(10):
        record_audit_event(f"event_{i}")
    assert len(get_audit_log(limit=3)) == 3
    assert len(get_audit_log(limit=100)) == 10

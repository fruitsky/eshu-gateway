def test_create_request_has_pending_status():
    from db.requests import create_request, get_all_requests
    rid = create_request("10.0.0.1", "uptime")
    reqs = get_all_requests()
    assert len(reqs) == 1
    assert reqs[0]["status"] == "pending"
    assert reqs[0]["target_ip"] == "10.0.0.1"
    assert reqs[0]["command"] == "uptime"
    assert reqs[0]["id"] == rid


def test_create_request_ttl():
    from db.requests import create_request, get_all_requests
    rid = create_request("10.0.0.1", "uptime")
    reqs = get_all_requests()
    assert reqs[0]["expires_at"] - reqs[0]["created_at"] == 90


def test_get_request_status_returns_string():
    from db.requests import create_request, get_request_status
    rid = create_request("10.0.0.1", "uptime")
    status = get_request_status(rid)
    assert status == "pending"
    assert isinstance(status, str)


def test_get_request_status_returns_none_for_missing():
    from db.requests import get_request_status
    assert get_request_status(99999) is None


def test_update_request_status_does_not_clobber_expires_at():
    from db.requests import create_request, update_request_status, get_all_requests
    import time
    rid = create_request("10.0.0.1", "uptime")
    before = get_all_requests()[0]
    time.sleep(1)
    update_request_status(rid, "approved")
    after = get_all_requests()[0]
    assert after["status"] == "approved"
    assert after["expires_at"] == before["expires_at"]


def test_update_request_status_to_denied():
    from db.requests import create_request, update_request_status, get_all_requests
    rid = create_request("10.0.0.1", "uptime")
    update_request_status(rid, "denied")
    assert get_all_requests()[0]["status"] == "denied"


def test_get_ticket_by_request_id_consumes_on_approved():
    from db.requests import create_request, update_request_status, get_ticket_by_request_id, get_request_status
    rid = create_request("10.0.0.1", "uptime")
    update_request_status(rid, "approved")
    ticket = get_ticket_by_request_id(rid)
    assert ticket is not None
    assert "uptime" in ticket["ticket"]
    assert get_request_status(rid) == "consumed"


def test_get_ticket_by_request_id_returns_none_for_pending():
    from db.requests import create_request, get_ticket_by_request_id
    rid = create_request("10.0.0.1", "uptime")
    assert get_ticket_by_request_id(rid) is None


def test_get_ticket_by_request_id_already_consumed():
    from db.requests import create_request, update_request_status, get_ticket_by_request_id
    rid = create_request("10.0.0.1", "uptime")
    update_request_status(rid, "approved")
    get_ticket_by_request_id(rid)
    ticket2 = get_ticket_by_request_id(rid)
    assert ticket2 is not None
    assert "uptime" in ticket2["ticket"]


def test_update_ticket_consumed_by_ip():
    from db.requests import create_request, update_request_status, update_ticket_consumed_by_ip, get_request_status
    rid = create_request("10.0.0.2", "docker ps")
    update_request_status(rid, "approved")
    ticket = update_ticket_consumed_by_ip("10.0.0.2")
    assert ticket is not None
    assert "docker ps" in ticket
    assert get_request_status(rid) == "consumed"


def test_update_ticket_consumed_by_ip_only_matches_target_ip():
    from db.requests import create_request, update_request_status, update_ticket_consumed_by_ip
    rid = create_request("10.0.0.3", "ls")
    update_request_status(rid, "approved")
    assert update_ticket_consumed_by_ip("10.0.0.99") is None
    from db.requests import get_request_status
    assert get_request_status(rid) == "approved"


def test_update_ticket_consumed_by_ip_does_not_match_consumed():
    from db.requests import create_request, update_request_status, update_ticket_consumed_by_ip
    rid = create_request("10.0.0.4", "test")
    update_request_status(rid, "consumed")
    assert update_ticket_consumed_by_ip("10.0.0.4") is None


def test_get_all_requests_returns_multiple():
    from db.requests import create_request, get_all_requests
    create_request("10.0.0.1", "cmd1")
    create_request("10.0.0.2", "cmd2")
    create_request("10.0.0.1", "cmd3")
    reqs = get_all_requests()
    assert len(reqs) == 3


def test_get_all_requests_orders_by_created_at_desc():
    import time
    from db.requests import create_request, get_all_requests
    r1 = create_request("10.0.0.1", "first")
    time.sleep(1.1)
    r2 = create_request("10.0.0.1", "second")
    reqs = get_all_requests()
    assert reqs[0]["id"] == r2
    assert reqs[1]["id"] == r1


def test_delete_old_requests():
    import time
    from db.requests import create_request, delete_old_requests, get_all_requests
    create_request("10.0.0.1", "old", ttl=1)
    assert len(get_all_requests()) == 1
    # Delete everything created strictly before "now + buffer" — the just-created
    # row is older than that, so it is always removed (deterministic; avoids the
    # second-boundary race between int(time.time()) truncation and created_at).
    delete_old_requests(int(time.time()) + 1)
    assert len(get_all_requests()) == 0


def test_search_requests():
    from db.requests import create_request, search_requests
    create_request("10.0.0.1", "apt update")
    create_request("10.0.0.2", "docker ps")
    results = search_requests("apt")
    assert len(results) == 1
    assert "apt update" in results[0]["command"]
    results = search_requests("10.0.0.1")
    assert len(results) == 1
    assert results[0]["target_ip"] == "10.0.0.1"


def test_create_request_honors_ttl():
    from db.requests import create_request, get_all_requests
    create_request("10.0.0.1", "quick", ttl=5)
    rid = get_all_requests()[0]
    assert rid["expires_at"] - rid["created_at"] == 5


def test_create_request_supports_reason():
    from db.requests import create_request, get_all_requests
    create_request("10.0.0.1", "cmd", reason="test reason")
    req = get_all_requests()[0]
    assert req["reason"] == "test reason"

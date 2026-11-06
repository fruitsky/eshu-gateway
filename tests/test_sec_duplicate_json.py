"""Chunk 1f: duplicate JSON keys on gateway-facing bodies must be refused.

An un-updated gateway builds JSON by concatenation, so an injected prefix value
can close its string and add a second ``status``/``encoded_command``. After
parsing, every value looks legitimate — only the duplicate key itself reveals
the injection.
"""
import json

import pytest

CONCISE = {"Content-Type": "application/json"}


def _post(client, headers, path, raw):
    return client.post(path, content=raw, headers={**headers, **CONCISE})


def _audit():
    from db.audit import get_audit_log
    return [e for e in get_audit_log()
            if e["event_type"] == "gateway_body_rejected"]


def test_sec_chunk1_duplicate_log_status_is_rejected(client, gateway_headers):
    from db.requests import get_all_requests
    raw = ('{"target_ip":"10.0.0.1","encoded_command":"dXB0aW1l",'
           '"status":"blocked","status":"approved"}')
    r = _post(client, gateway_headers, "/api/log", raw)
    assert r.status_code == 400
    assert get_all_requests() == []
    events = _audit()
    assert events
    assert events[0]["details"] == "duplicate JSON keys: status"


def test_sec_chunk1_duplicate_request_command_is_rejected(client, gateway_headers):
    from db.requests import get_all_requests
    raw = ('{"target_ip":"10.0.0.1",'
           '"encoded_command":"dXB0aW1l",'
           '"encoded_command":"ZWNobyBwd25lZA=="}')
    r = _post(client, gateway_headers, "/api/request", raw)
    assert r.status_code == 400
    assert get_all_requests() == []
    assert "encoded_command" in (_audit()[0]["details"] or "")


def test_sec_chunk1_duplicate_nested_key_is_rejected(client, gateway_headers):
    # Duplicates are refused at any nesting level, not just the top object.
    raw = ('{"target_ip":"10.0.0.1","encoded_command":"dXB0aW1l",'
           '"status":"blocked","nested":{"a":1,"a":2}}')
    r = _post(client, gateway_headers, "/api/log", raw)
    assert r.status_code == 400
    assert "a" in (_audit()[0]["details"] or "")


def test_sec_chunk1_duplicate_heartbeat_key_is_rejected(client, gateway_headers):
    raw = '{"ip":"10.0.0.1","hostname":"first","hostname":"second"}'
    r = _post(client, gateway_headers, "/api/gateway-heartbeat", raw)
    assert r.status_code == 400
    assert "hostname" in (_audit()[0]["details"] or "")


def test_sec_chunk1_duplicate_key_audit_never_records_values(
        client, gateway_headers):
    secret = "supersecret-value"
    raw = ('{"target_ip":"10.0.0.1","encoded_command":"dXB0aW1l",'
           '"status":"blocked","status":"%s"}' % secret)
    r = _post(client, gateway_headers, "/api/log", raw)
    assert r.status_code == 400
    details = _audit()[0]["details"] or ""
    assert secret not in details
    assert "status" in details


def test_sec_chunk1_legitimate_json_still_accepted(client, gateway_headers):
    raw = json.dumps({
        "target_ip": "10.0.0.1",
        "encoded_command": "dXB0aW1l",
        "status": "blocked",
    })
    r = _post(client, gateway_headers, "/api/log", raw)
    assert r.status_code == 200


def test_sec_chunk1_non_json_body_keeps_existing_status(client, gateway_headers):
    r = client.post("/api/log", content="not json",
                    headers={**gateway_headers,
                             "Content-Type": "text/plain"})
    assert r.status_code == 422
    assert _audit() == []


@pytest.mark.parametrize("path", ["/api/request", "/api/log"])
def test_sec_chunk1_duplicate_key_is_refused_before_any_store(
        client, gateway_headers, path):
    from db.requests import get_all_requests
    # Duplicate a field whose final value is legitimate, so only the
    # duplicate key (not a value check) can reject the body.
    raw = ('{"target_ip":"10.0.0.1","target_ip":"10.0.0.1",'
           '"encoded_command":"dXB0aW1l","status":"blocked"}')
    r = _post(client, gateway_headers, path, raw)
    assert r.status_code == 400
    assert get_all_requests() == []

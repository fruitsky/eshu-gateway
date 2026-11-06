"""Chunk 1 regressions: /api/log status lockdown and gateway metadata validation.

An old (un-updated) gateway inserts prefix metadata into its JSON bodies by
string concatenation, so the dashboard is the fleet-wide boundary: a status it
does not recognise must never be stored, and an execution/session id outside
the gateway's allowed charset must never reach the request table.
"""
import base64

import pytest


def b64(s):
    return base64.b64encode(s.encode()).decode()


ALLOWED_STATUSES = [
    "auto-approved", "blocked", "frozen", "session-required", "window-rejected",
]
REJECTED_STATUSES = [
    "approved", "pending", "consumed", "fleet-run", "window-approved",
]


def _rows():
    from db.requests import get_all_requests
    return get_all_requests()


@pytest.mark.parametrize("status", ALLOWED_STATUSES)
def test_sec_chunk1_allowed_log_status_is_stored(client, gateway_headers, status):
    r = client.post("/api/log", json={
        "target_ip": "10.0.0.1",
        "encoded_command": b64("uptime"),
        "status": status,
    }, headers=gateway_headers)
    assert r.status_code == 200
    assert any(row["status"] == status and row["command"] == "uptime"
               for row in _rows())


@pytest.mark.parametrize("status", REJECTED_STATUSES)
def test_sec_chunk1_rejected_log_status_stores_nothing(client, gateway_headers,
                                                       status):
    r = client.post("/api/log", json={
        "target_ip": "10.0.0.1",
        "encoded_command": b64("uptime"),
        "status": status,
    }, headers=gateway_headers)
    assert r.status_code == 400
    assert not any(row["status"] == status for row in _rows())


def test_sec_chunk1_injected_approved_status_cannot_mint_a_ticket(
        client, gateway_headers):
    # Attack 1: /api/log with status="approved" would be picked up by
    # update_ticket_consumed_by_ip() on the next poll and turned into a ticket.
    r = client.post("/api/log", json={
        "target_ip": "10.0.0.1",
        "encoded_command": b64("id"),
        "status": "approved",
    }, headers=gateway_headers)
    assert r.status_code == 400
    poll = client.get("/api/poll/10.0.0.1", headers=gateway_headers)
    assert poll.status_code == 200
    assert poll.json()["ticket"] is None


@pytest.mark.parametrize("field,value", [
    ("session_id", 'x","status":"approved'),
    ("execution_id", 'x","status":"approved'),
    ("session_id", "bad id"),
    ("execution_id", "bad/id"),
])
def test_sec_chunk1_request_rejects_invalid_metadata(client, gateway_headers,
                                                     field, value):
    r = client.post("/api/request", json={
        "target_ip": "10.0.0.1",
        "encoded_command": b64("uptime"),
        field: value,
    }, headers=gateway_headers)
    assert r.status_code == 400


def test_sec_chunk1_request_accepts_valid_metadata(client, gateway_headers):
    r = client.post("/api/request", json={
        "target_ip": "10.0.0.1",
        "encoded_command": b64("uptime"),
        "session_id": "chat_valid-1",
        "execution_id": "exec.v1:2",
    }, headers=gateway_headers)
    assert r.status_code == 200
    row = _rows()[0]
    assert row["session_id"] == "chat_valid-1"
    assert row["execution_id"] == "exec.v1:2"


@pytest.mark.parametrize("field,value", [
    ("session_id", 'x","status":"approved'),
    ("execution_id", "bad/id"),
])
def test_sec_chunk1_log_rejects_invalid_metadata(client, gateway_headers,
                                                 field, value):
    r = client.post("/api/log", json={
        "target_ip": "10.0.0.1",
        "encoded_command": b64("uptime"),
        "status": "blocked",
        field: value,
    }, headers=gateway_headers)
    assert r.status_code == 400
    assert not any(row["command"] == "uptime" for row in _rows())


def test_sec_chunk1_log_reason_is_capped(client, gateway_headers):
    r = client.post("/api/log", json={
        "target_ip": "10.0.0.1",
        "encoded_command": b64("uptime"),
        "status": "blocked",
        "reason": "r" * 300,
    }, headers=gateway_headers)
    assert r.status_code == 200
    row = next(row for row in _rows() if row["command"] == "uptime")
    assert len(row["reason"]) == 200

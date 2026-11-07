"""Chunk 1g: old-gateway -> new-dashboard compatibility and hostile replay.

The pre-chunk gateway (625995d) is pinned under fixtures/legacy so this suite
proves the dashboard accepts every legitimate body an un-updated gateway sends,
while rejecting the injected duplicates such a gateway can still produce.
"""
from pathlib import Path

from urllib.parse import urlparse

from test_gateway_script import GatewaySandbox

LEGACY = (Path(__file__).resolve().parent
          / "fixtures" / "legacy" / "eshu-gateway-625995d.sh")


def _calls(gw, endpoint):
    return [(call, raw) for call, raw in zip(gw.curl_calls(), gw.raw_bodies())
            if call["url"].endswith(endpoint)]


def _replay(client, headers, call, raw):
    return client.post(
        urlparse(call["url"]).path,
        content=raw.encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
    )


def _replay_all(client, headers, gw, endpoint):
    calls = _calls(gw, endpoint)
    assert calls, "legacy gateway recorded no %s call" % endpoint
    for call, raw in calls:
        r = _replay(client, headers, call, raw)
        assert r.status_code == 200, (call["url"], raw, r.text)


# ── legitimate flows from the pinned pre-chunk gateway ─────────────────────
def test_sec_compat_legacy_allowlist_log_replays(client, gateway_headers, tmp_path):
    gw = GatewaySandbox(tmp_path, script=LEGACY)
    gw.exact("echo compat-allow")
    assert gw.run("echo compat-allow").returncode == 0
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/log") for c in calls))
    _replay_all(client, gateway_headers, gw, "/api/log")


def test_sec_compat_legacy_blocked_log_replays(client, gateway_headers, tmp_path):
    gw = GatewaySandbox(tmp_path, script=LEGACY)
    gw.blocklist("compat-forbidden")
    proc = gw.run("echo compat-forbidden")
    assert "blacklist match" in proc.stdout
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/log")
        and c["data"].get("status") == "blocked" for c in calls))
    _replay_all(client, gateway_headers, gw, "/api/log")


def test_sec_compat_legacy_freeze_log_replays(client, gateway_headers, tmp_path):
    gw = GatewaySandbox(tmp_path, script=LEGACY)
    gw.freeze(True)
    gw.run("echo compat-frozen")
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/log")
        and c["data"].get("status") == "frozen" for c in calls))
    _replay_all(client, gateway_headers, gw, "/api/log")


def test_sec_compat_legacy_session_required_log_replays(
        client, gateway_headers, tmp_path):
    gw = GatewaySandbox(tmp_path, script=LEGACY)
    gw.session_key()
    gw.require_sessions(True)
    gw.run("echo compat-session")
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/log")
        and c["data"].get("status") == "session-required" for c in calls))
    _replay_all(client, gateway_headers, gw, "/api/log")


def test_sec_compat_legacy_window_reject_log_replays(
        client, gateway_headers, tmp_path):
    gw = GatewaySandbox(tmp_path, script=LEGACY)
    gw.feature("window.sh", 'log_window_reject "$cmd" "unknown window token"\n')
    gw.jit_status("denied")
    gw.run("ESHU_WINDOW_TOKEN=abcdef echo compat-window")
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/log")
        and c["data"].get("status") == "window-rejected" for c in calls))
    _replay_all(client, gateway_headers, gw, "/api/log")


def test_sec_compat_legacy_jit_request_replays(client, gateway_headers, tmp_path):
    gw = GatewaySandbox(tmp_path, script=LEGACY)
    gw.jit_status("denied")
    proc = gw.run("echo compat-jit")
    assert "[DENIED]" in proc.stdout
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/request") for c in calls))
    _replay_all(client, gateway_headers, gw, "/api/request")


# ── hostile bodies from the same gateway must be rejected ──────────────────
def _rejected_without_a_ticket(client, gateway_headers, gw, endpoint):
    from db.requests import get_all_requests
    from db.audit import get_audit_log

    before = len(get_all_requests())
    calls = _calls(gw, endpoint)
    assert calls, "legacy gateway recorded no %s call" % endpoint
    for call, raw in calls:
        assert r'"' in raw
        r = _replay(client, gateway_headers, call, raw)
        assert r.status_code == 400, (call["url"], raw, r.text)
    assert len(get_all_requests()) == before
    poll = client.get("/api/poll/10.0.0.1", headers=gateway_headers)
    assert poll.status_code == 200
    assert poll.json()["ticket"] is None
    rejected = [e for e in get_audit_log()
                if e["event_type"] == "gateway_body_rejected"]
    assert rejected
    details = rejected[0]["details"] or ""
    assert "duplicate JSON keys:" in details
    assert "ZWNobyBwd25lZA==" not in details  # names, never values


def test_sec_compat_legacy_injected_status_is_rejected(
        client, gateway_headers, tmp_path):
    gw = GatewaySandbox(tmp_path, script=LEGACY)
    gw.freeze(True)
    gw.run('ESHU_SESSION_ID=x","status":"approved echo hello')
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/log") for c in calls))
    raw = _calls(gw, "/api/log")[0][1]
    assert raw.count('"status"') >= 2
    _rejected_without_a_ticket(client, gateway_headers, gw, "/api/log")


def test_sec_compat_legacy_injected_command_is_rejected(
        client, gateway_headers, tmp_path):
    gw = GatewaySandbox(tmp_path, script=LEGACY)
    gw.exact("echo hello")
    gw.run('ESHU_EXECUTION_ID=x","encoded_command":"ZWNobyBwd25lZA== echo hello')
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/log") for c in calls))
    raw = _calls(gw, "/api/log")[0][1]
    assert raw.count('"encoded_command"') >= 2
    _rejected_without_a_ticket(client, gateway_headers, gw, "/api/log")


def test_sec_compat_legacy_injected_command_in_jit_request_is_rejected(
        client, gateway_headers, tmp_path):
    gw = GatewaySandbox(tmp_path, script=LEGACY)
    gw.jit_status("denied")
    gw.run('ESHU_EXECUTION_ID=x","encoded_command":"ZWNobyBwd25lZA== echo jit')
    assert gw.wait_for(lambda calls: any(
        c["url"].endswith("/api/request") for c in calls))
    raw = _calls(gw, "/api/request")[0][1]
    assert raw.count('"encoded_command"') >= 2
    _rejected_without_a_ticket(client, gateway_headers, gw, "/api/request")


# ── Chunk R2: legacy gateway + current window feature script ──────────────
def test_sec_R_window_exact_claim_200_runs(tmp_path):
    gw = GatewaySandbox(tmp_path, script=LEGACY)
    gw.approved_windows("wintok|echo window-ok|0|0|0|0|0|exact")
    gw.window_claim("200")
    proc = gw.run("ESHU_WINDOW_TOKEN=wintok echo window-ok")
    assert "window-ok" in proc.stdout
    assert proc.returncode == 0


def test_sec_R_window_prefix_claim_200_runs(tmp_path):
    gw = GatewaySandbox(tmp_path, script=LEGACY)
    gw.approved_windows("wintok|echo prefix|0|0|0|0|0|prefix")
    gw.window_claim("200")
    proc = gw.run("ESHU_WINDOW_TOKEN=wintok echo prefix-ok")
    assert "prefix-ok" in proc.stdout
    assert proc.returncode == 0


def test_sec_R_window_claim_404_is_rejected(tmp_path):
    gw = GatewaySandbox(tmp_path, script=LEGACY)
    gw.approved_windows("wintok|echo window-ok|0|0|0|0|0|exact")
    gw.window_claim("404")
    proc = gw.run("ESHU_WINDOW_TOKEN=wintok echo window-ok")
    assert "Window rejected" in proc.stdout
    assert "window-ok" not in proc.stdout
    assert proc.returncode == 1


def test_sec_R_window_claim_network_failure_runs(tmp_path):
    gw = GatewaySandbox(tmp_path, script=LEGACY)
    gw.approved_windows("wintok|echo window-ok|0|0|0|0|0|exact")
    gw.window_claim("exit")
    proc = gw.run("ESHU_WINDOW_TOKEN=wintok echo window-ok")
    assert "window-ok" in proc.stdout
    assert proc.returncode == 0

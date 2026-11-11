"""F12 — the gateway half: signed prefixes, the nonce store, envelopes, tickets.

These tests run the real `dashboard/eshu-gateway.sh` in the sandbox built by
`tests/test_gateway_script.py`: no root, no network, no real /etc. The
dashboard half lives in `tests/test_f12_ssh_exec.py`.
"""
import json
import os
import time

import pytest

from core.ssh_exec import parse_envelope
from test_gateway_script import GatewaySandbox

IP = "10.0.0.1"


@pytest.fixture
def gw(tmp_path):
    sandbox = GatewaySandbox(tmp_path)
    sandbox.token("test-token")
    return sandbox


def envelope(proc):
    parsed = parse_envelope(proc.stdout)
    assert "error" not in parsed, proc.stdout
    return parsed


def output_of(proc):
    import base64
    parsed = envelope(proc)
    return base64.b64decode(parsed["output_b64"]).decode()


def messages_of(proc):
    return " ".join(envelope(proc)["messages"])


# ── the happy path ──────────────────────────────────────────────────────────
def test_signed_prefix_runs_the_allowlisted_command(gw):
    gw.exact("echo hello")
    proc = gw.run(gw.sign("echo hello"))
    parsed = envelope(proc)
    assert parsed["status"] == "ran"
    assert parsed["exit_code"] == 0
    assert output_of(proc) == "hello\n"
    # the envelope is the only stdout line
    assert proc.stdout.count("ESHU-MCP-ENVELOPE") == 1


def test_exit_code_is_a_field_not_a_line(gw):
    gw.exact("false")
    proc = gw.run(gw.sign("false"))
    parsed = envelope(proc)
    assert parsed["status"] == "ran" and parsed["exit_code"] == 1
    assert "[ESHU exit=" not in proc.stdout


def test_output_is_truncated_to_64k(gw):
    # No metacharacters in the command itself (a `|` would route it to JIT);
    # the script does the piping.
    cmd = gw.command_script("bigout", "head -c 70000 /dev/zero | base64")
    gw.exact(cmd)
    proc = gw.run(gw.sign(cmd))
    parsed = envelope(proc)
    assert parsed["truncated"] is True
    import base64
    assert len(base64.b64decode(parsed["output_b64"])) == 65536


# ── the MAC ────────────────────────────────────────────────────────────────
def test_a_different_command_is_refused(gw):
    gw.exact("echo hello")
    prefix = gw.sign("echo hello")
    parts = prefix.rsplit(" ", 1)
    proc = gw.run(parts[0] + " whoami")
    assert envelope(proc)["status"] == "refused"
    assert "Invalid ESHU_AGENT_SIG" in messages_of(proc)
    assert proc.returncode == 1


def test_a_flipped_mcp_flag_is_refused(gw):
    gw.exact("echo hello")
    line = gw.sign("echo hello").replace("ESHU_MCP=1", "ESHU_MCP=1", 1)
    # Re-sign nothing: flip FORCE_JIT instead, which the MAC also covers.
    proc = gw.run(line.replace("ESHU_MCP=1", "ESHU_MCP=1 ESHU_FORCE_JIT=1"))
    assert envelope(proc)["status"] == "refused"


def test_a_stale_timestamp_is_refused(gw):
    gw.exact("echo hello")
    proc = gw.run(gw.sign("echo hello", ts=int(time.time()) - 600))
    assert envelope(proc)["status"] == "refused"
    assert "Stale ESHU_TS" in messages_of(proc)


def test_a_bad_signature_is_refused(gw):
    gw.exact("echo hello")
    line = gw.sign("echo hello").replace("ESHU_AGENT_SIG=", "ESHU_AGENT_SIG=0", 1)
    proc = gw.run(line)
    assert envelope(proc)["status"] == "refused"


def test_a_signature_for_another_gateway_is_refused(gw):
    gw.exact("echo hello")
    other = GatewaySandbox(gw.root / "other", target_ip="10.0.0.2")
    other.token("test-token")
    proc = gw.run(other.sign("echo hello"))
    assert envelope(proc)["status"] == "refused"


def test_a_replayed_prefix_is_refused(gw):
    gw.exact("echo hello")
    line = gw.sign("echo hello")
    assert envelope(gw.run(line))["status"] == "ran"
    proc = gw.run(line)
    assert envelope(proc)["status"] == "refused"
    assert "already used" in messages_of(proc)


# ── G2: half-signed and ambiguous ──────────────────────────────────────────
@pytest.mark.parametrize("prefix", ["ESHU_AGENT=agent-a",
                                    "ESHU_FORCE_JIT=1", "ESHU_TS=1"])
def test_half_signed_prefix_is_refused_in_direct_mode(gw, prefix):
    gw.exact("echo hello")
    proc = gw.run(prefix + " echo hello")
    assert proc.returncode == 1
    assert "Incomplete ESHU agent prefix" in proc.stdout
    assert gw.curl_calls() == []


def test_a_lone_mcp_flag_is_refused(gw):
    gw.exact("echo hello")
    proc = gw.run("ESHU_MCP=1 echo hello")
    assert envelope(proc)["status"] == "refused"
    assert gw.curl_calls() == []


def test_a_duplicate_prefix_key_is_refused(gw):
    gw.exact("echo hello")
    line = gw.sign("echo hello")
    agent = line.split()[0]
    proc = gw.run(line.replace(agent, agent + " " + agent, 1))
    assert proc.returncode == 1
    assert "Duplicate" in proc.stdout
    assert gw.curl_calls() == []


def test_an_incomplete_prefix_is_refused(gw):
    gw.exact("echo hello")
    line = gw.sign("echo hello")
    # Drop the nonce: a partial identity is refused, never defaulted.
    line = " ".join(part for part in line.split() if not part.startswith("ESHU_NONCE="))
    proc = gw.run(line)
    assert envelope(proc)["status"] == "refused"
    assert "Incomplete" in messages_of(proc)


def test_mcp_only_refuses_direct_ssh(gw):
    gw.exact("echo hello")
    gw.ssh_mode("mcp_only")
    proc = gw.run("echo hello")
    assert proc.returncode == 1
    assert "mcp_only" in proc.stdout


def test_mcp_only_accepts_a_signed_call(gw):
    gw.exact("echo hello")
    gw.ssh_mode("mcp_only")
    assert envelope(gw.run(gw.sign("echo hello")))["status"] == "ran"


# ── G6: the dashboard key marker ───────────────────────────────────────────
def test_dashboard_key_without_a_signature_is_refused(gw):
    gw.exact("echo hello")
    proc = gw.run("echo hello", args=["--dashboard-key"])
    assert proc.returncode == 1
    assert "dashboard key" in proc.stdout
    assert gw.curl_calls() == []


def test_dashboard_key_refuses_a_signed_call_with_a_bad_mac(gw):
    gw.exact("echo hello")
    line = gw.sign("echo hello").replace("ESHU_AGENT_SIG=", "ESHU_AGENT_SIG=0", 1)
    proc = gw.run(line, args=["--dashboard-key"])
    assert envelope(proc)["status"] == "refused"


def test_dashboard_key_with_a_signature_runs(gw):
    gw.exact("echo hello")
    proc = gw.run(gw.sign("echo hello"), args=["--dashboard-key"])
    assert envelope(proc)["status"] == "ran"


def test_direct_ssh_still_works_without_the_marker(gw):
    gw.exact("echo hello")
    proc = gw.run("echo hello")
    assert proc.returncode == 0
    assert "hello" in proc.stdout


# ── the envelope on every path ─────────────────────────────────────────────
def test_frozen_path_is_one_envelope(gw):
    gw.exact("echo hello")
    gw.freeze(True)
    proc = gw.run(gw.sign("echo hello"))
    parsed = envelope(proc)
    assert parsed["status"] == "refused"
    assert "FROZEN" in " ".join(parsed["messages"])
    assert gw.wait_for(lambda calls: any(
        c["data"].get("status") == "frozen" for c in calls))


def test_blocked_path_is_one_envelope(gw):
    gw.blocklist("echo")
    proc = gw.run(gw.sign("echo hello"))
    assert envelope(proc)["status"] == "refused"
    assert proc.stdout.count("ESHU-MCP-ENVELOPE") == 1


def test_pending_returns_an_integer_request_id(gw):
    proc = gw.run(gw.sign("echo hello"))
    parsed = envelope(proc)
    assert parsed["status"] == "pending"
    assert parsed["request_id"] == 123
    assert isinstance(parsed["request_id"], int)
    assert "check_request(123)" in " ".join(parsed["messages"])


def test_pending_does_not_poll(gw):
    """MCP mode returns immediately; the old auto-poll would sleep 2 s here."""
    started = time.time()
    proc = gw.run(gw.sign("echo hello"))
    assert envelope(proc)["status"] == "pending"
    assert time.time() - started < 5


def test_force_jit_skips_the_allowlist(gw):
    gw.exact("echo hello")
    proc = gw.run(gw.sign("echo hello", force_jit="1"))
    assert envelope(proc)["status"] == "pending"
    assert gw.curl_calls()


# ── G8: logging ────────────────────────────────────────────────────────────
def test_log_line_has_the_stripped_command_and_no_prefix_values(gw):
    gw.exact("echo hello")
    line = gw.sign("echo hello")
    sig = [p for p in line.split() if p.startswith("ESHU_AGENT_SIG=")][0]
    nonce = [p for p in line.split() if p.startswith("ESHU_NONCE=")][0]
    gw.run(line)
    text = gw.logger_text()
    cmd_lines = [l for l in text.splitlines() if "cmd=echo hello" in l]
    assert cmd_lines
    assert sig not in text and nonce not in text
    assert "ESHU_AGENT_SIG" not in text and "ESHU_NONCE" not in text


def test_refusal_log_has_no_raw_value(gw):
    gw.run("ESHU_SESSION_ID=bad id echo x")
    assert "bad id" not in gw.logger_text()


# ── G4: the nonce store ───────────────────────────────────────────────────
def _nonce_lines(gw):
    path = gw.nonce_dir / "nonces"
    if not path.exists():
        return []
    return [l for l in path.read_text().splitlines() if l.strip()]


def test_nonce_store_is_persistent_not_in_run(gw):
    gw.exact("echo hello")
    gw.run(gw.sign("echo hello"))
    assert len(_nonce_lines(gw)) == 1
    assert gw.nonce_dir.exists()
    assert str(gw.nonce_dir).startswith(str(gw.root))
    # A replay after the capture dir is recreated is still refused: the store
    # is not in it.
    import shutil
    shutil.rmtree(gw.capture_dir)
    gw.capture_dir.mkdir(mode=0o700)
    proc = gw.run(gw.sign("echo hello", nonce=_nonce_lines(gw)[0].split()[1]))
    assert envelope(proc)["status"] == "refused"


def test_a_bad_mac_adds_no_entry(gw):
    gw.exact("echo hello")
    line = gw.sign("echo hello").replace("ESHU_AGENT_SIG=", "ESHU_AGENT_SIG=0", 1)
    gw.run(line)
    assert _nonce_lines(gw) == []


def test_an_unwritable_store_refuses_and_keeps_the_old_entries(gw):
    gw.exact("echo hello")
    assert envelope(gw.run(gw.sign("echo hello")))["status"] == "ran"
    before = _nonce_lines(gw)
    os.chmod(gw.nonce_dir, 0o500)
    try:
        proc = gw.run(gw.sign("echo hello"))
        assert envelope(proc)["status"] == "refused"
    finally:
        os.chmod(gw.nonce_dir, 0o700)
    assert _nonce_lines(gw) == before


def test_a_full_store_refuses(gw):
    gw.exact("echo hello")
    now = int(time.time())
    (gw.nonce_dir / "nonces").write_text(
        "".join(f"{now} {'%032x' % i}\n" for i in range(4096)))
    proc = gw.run(gw.sign("echo hello"))
    assert envelope(proc)["status"] == "refused"
    assert "full" in messages_of(proc)
    assert len(_nonce_lines(gw)) == 4096


# ── G5: tickets ────────────────────────────────────────────────────────────
def _ticket(gw, command, agent="agent-a", session="sess-1", ts=None):
    ts = int(ts if ts is not None else time.time())
    return f"v2|{ts}|{agent}|{session}|{command}"


def test_v2_ticket_is_claimed_by_its_agent(gw):
    gw.tickets(_ticket(gw, "echo hello"))
    before = _lockbox(gw)
    proc = gw.run(gw.sign("echo hello", agent="agent-a", session="sess-1"))
    parsed = envelope(proc)
    assert parsed["status"] == "ran"
    assert output_of(proc) == "hello\n"
    assert _lockbox(gw) != before          # claimed and burned

    # ... and exactly once: the same signed bytes go to a fresh request.
    proc = gw.run(gw.sign("echo hello", agent="agent-a", session="sess-1"))
    assert envelope(proc)["status"] == "pending"


def _lockbox(gw):
    if not gw.ticket_file.exists():
        return ""
    return gw.ticket_file.read_text()


def test_another_agent_cannot_claim_it(gw):
    gw.tickets(_ticket(gw, "echo hello"))
    before = _lockbox(gw)
    proc = gw.run(gw.sign("echo hello", agent="agent-b", session="sess-2"))
    # It falls back to a fresh request instead of taking agent-a's approval.
    assert envelope(proc)["status"] == "pending"
    assert _lockbox(gw) == before


def test_a_different_session_cannot_claim_it(gw):
    gw.tickets(_ticket(gw, "echo hello", session="sess-1"))
    before = _lockbox(gw)
    proc = gw.run(gw.sign("echo hello", agent="agent-a", session="sess-9"))
    assert envelope(proc)["status"] == "pending"
    assert _lockbox(gw) == before


def test_an_unsigned_caller_cannot_claim_it(gw):
    gw.tickets(_ticket(gw, "echo hello"))
    before = _lockbox(gw)
    proc = gw.run("echo hello")
    assert proc.returncode == 1          # it never ran from that ticket
    assert "hello" not in proc.stdout
    assert _lockbox(gw) == before


def test_an_unsigned_caller_claims_an_old_line(gw):
    gw.tickets(f"{int(time.time())}|echo hello")
    proc = gw.run("echo hello")
    assert proc.returncode == 0
    assert "hello" in proc.stdout


def test_a_signed_caller_cannot_claim_an_old_line(gw):
    gw.tickets(f"{int(time.time())}|echo hello")
    before = _lockbox(gw)
    proc = gw.run(gw.sign("echo hello", agent="agent-a", session="sess-1"))
    assert envelope(proc)["status"] == "pending"
    assert _lockbox(gw) == before


def test_an_old_line_keeps_a_command_with_pipes(gw):
    gw.tickets(f"{int(time.time())}|echo a|tr a b")
    proc = gw.run("echo a|tr a b")
    assert proc.returncode == 0
    assert "b" in proc.stdout


def test_an_old_line_that_looks_like_a_ticket_stays_agentless(gw):
    # The command itself looks like `agent|session|…`; without the v2 tag it
    # must never be read as a ticket for a shorter command.
    gw.tickets(f"{int(time.time())}|agent-a|sess-1|echo hello")
    before = _lockbox(gw)
    proc = gw.run(gw.sign("echo hello", agent="agent-a", session="sess-1"))
    assert envelope(proc)["status"] == "pending"
    assert _lockbox(gw) == before


def test_a_v2_line_whose_command_contains_pipes(gw):
    gw.tickets(_ticket(gw, "echo a|tr a b"))
    proc = gw.run(gw.sign("echo a|tr a b", agent="agent-a", session="sess-1"))
    assert envelope(proc)["status"] == "ran"
    import base64
    assert "b" in base64.b64decode(envelope(proc)["output_b64"]).decode()


# ── the session gate (G2) ─────────────────────────────────────────────────
def test_a_valid_agent_mac_vouches_for_the_session(gw):
    gw.exact("echo hello")
    gw.require_sessions(True)
    gw.session_key()
    proc = gw.run(gw.sign("echo hello", session="sess-1"))
    assert envelope(proc)["status"] == "ran"


def test_direct_ssh_still_needs_the_session_signature(gw):
    gw.exact("echo hello")
    gw.require_sessions(True)
    gw.session_key()
    proc = gw.run("ESHU_SESSION_ID=sess-1 echo hello")
    assert proc.returncode == 1
    assert "Session id/signature is invalid" in proc.stdout


def test_an_unsigned_session_still_needs_a_signature(gw):
    gw.exact("echo hello")
    gw.require_sessions(True)
    gw.session_key()
    proc = gw.run("ESHU_SESSION_ID=sess-1 echo hello")
    assert proc.returncode == 1


# ── code round 1: R2, R3, S1, S2 and R4 ───────────────────────────────────
def test_an_unknown_eshu_key_is_refused_direct(gw):
    """R3/G2: the gateway refuses what the dashboard refuses for signed calls.

    A caller on the shared key reaches the script directly; an unknown key
    stops the prefix parser, and without this check it would run as part of
    the command (or slip past the allowlist as `ESHU_...` noise).
    """
    gw.exact("echo hello")
    proc = gw.run("ESHU_BOGUS=1 echo hello")
    assert proc.returncode == 1
    assert "Unknown ESHU_ prefix" in proc.stdout
    assert "hello" not in proc.stdout


def test_a_known_key_after_an_unknown_one_is_refused(gw):
    gw.exact("echo hello")
    proc = gw.run("ESHU_BOGUS=1 ESHU_AGENT=agent-a echo hello")
    assert proc.returncode == 1
    assert "Unknown ESHU_ prefix" in proc.stdout


def test_an_unknown_eshu_key_is_refused_in_mcp_mode(gw):
    gw.exact("echo hello")
    proc = gw.run(gw.sign("ESHU_BOGUS=1 echo hello"))
    assert envelope(proc)["status"] == "refused"
    assert "Unknown ESHU_ prefix" in messages_of(proc)


def test_a_signed_prefix_must_not_carry_unmaced_keys(gw):
    """S1: WINDOW_TOKEN and EXECUTION_ID are outside the MAC."""
    gw.exact("echo hello")
    for key in ("ESHU_WINDOW_TOKEN=WinToken123", "ESHU_EXECUTION_ID=exec-1"):
        proc = gw.run(key + " " + gw.sign("echo hello"))
        assert envelope(proc)["status"] == "refused", key
        assert "must not carry" in messages_of(proc), key


def test_the_nonce_store_is_self_protected(gw):
    """S2: deleting /var/lib/eshu would reopen the replay window."""
    proc = gw.run("rm -rf /var/lib/eshu")
    assert proc.returncode == 1
    assert "FATAL" in proc.stdout
    assert "HARDCODED BLOCK" in gw.logger_text()


def test_the_refusal_log_carries_no_unverified_agent(gw):
    """R2: a value that failed its charset check must not reach syslog."""
    gw.exact("echo hello")
    proc = gw.run("ESHU_AGENT=bad\x01agent echo hello")
    assert proc.returncode == 1
    text = gw.logger_text()
    assert "\x01" not in text
    assert "bad\x01agent" not in text
    assert "agent=none" in text


def _result_post(gw):
    assert gw.wait_for(lambda calls: any(
        c['url'].endswith('/api/exec-result') for c in calls)), gw.curl_calls()
    for call in gw.curl_calls():
        if call['url'].endswith('/api/exec-result'):
            return call['data']
    raise AssertionError('no result post')


def test_mcp_exit_only_stores_no_output(gw):
    """R4: the operator's capture setting wins in MCP mode too (§2.5)."""
    gw.exact("echo hello")
    gw.capture("exit-only")
    proc = gw.run(gw.sign("echo hello"))
    assert envelope(proc)["status"] == "ran"
    body = _result_post(gw)
    assert body["encoded_output"] == ""
    assert body["exit_code"] == 0


def test_mcp_tail_still_stores_the_tail(gw):
    gw.exact("echo hello")
    gw.capture("tail")
    gw.run(gw.sign("echo hello"))
    body = _result_post(gw)
    import base64
    assert base64.b64decode(body["encoded_output"]) == b"hello\n"

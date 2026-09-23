"""Regression tests for the gateway installer template.

The write_poller/write_gateway `__GATEWAY_TOKEN__` substitution must be scoped to
the header assignment ONLY (the poller/gateway headers each carry exactly one
placeholder). Token self-heal was removed from the poller — recovery from a lost
token is now re-enrollment — so nothing else depends on the placeholder.
"""
import os
import re

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def _read(path):
    with open(os.path.join(ROOT, path), "r", encoding="utf-8") as f:
        return f.read()


def test_write_poller_sed_is_scoped_to_header_assignment():
    poller = _read("dashboard/eshu-poller.sh")
    # Apply the exact (fixed) write_poller sed: header assignment line only.
    fixed = re.sub(r'^GATEWAY_TOKEN="__GATEWAY_TOKEN__"',
                   'GATEWAY_TOKEN="tok123"', poller, flags=re.M)
    # Header replaced with the token, and the placeholder is fully consumed.
    assert 'GATEWAY_TOKEN="tok123"' in fixed
    assert "__GATEWAY_TOKEN__" not in fixed
    # The dead self-heal block is gone (recovery is re-enrollment now).
    assert "self_heal" not in poller
    assert 'GATEWAY_TOKEN" = "__GATEWAY_TOKEN__"' not in poller


def test_logger_heartbeat_sends_gateway_token():
    logger = _read("dashboard/eshu-logger.sh")
    # The heartbeat authenticates with the gateway token, read at runtime from
    # the gateway script (never templated into the logger).
    assert "X-Gateway-Token" in logger
    assert "eshu-gateway.sh" in logger
    assert "__GATEWAY_TOKEN__" not in logger


def test_uninstall_report_sends_gateway_token():
    template = _read("dashboard/eshu-installer-template.sh")
    # Token is read from the existing gateway script before it is removed.
    assert "grep -oP '^GATEWAY_TOKEN=\"\\K[^\"]+'" in template
    # The uninstall _report() authenticates its progress posts.
    m = re.search(r'# Helper: report progress to dashboard\n\s*_report\(\) \{(.*?)\n  \}',
                  template, re.S)
    assert m, "uninstall _report() not found"
    assert "X-Gateway-Token" in m.group(1)


def test_template_has_no_global_token_sed():
    template = _read("dashboard/eshu-installer-template.sh")
    # The old global token substitution must be gone from write_gateway/write_poller.
    assert 's|__GATEWAY_TOKEN__|${GATEWAY_TOKEN:-}|g' not in template


def test_template_no_longer_requests_or_installs_approver_key():
    template = _read("dashboard/eshu-installer-template.sh")
    # The legacy approver key (operator root SSH key bypassing the gateway) was
    # removed from the product entirely. The template must not request it, parse
    # it as an argument, append it to /root/.ssh/authorized_keys, or carry any
    # transition hook for it — the fleet migration (v15.11) has completed.
    assert 'APPROVER_PUB_KEY="$2"' not in template
    assert "Enter SSH pubkey for APPROVER" not in template
    assert '$APPROVER_PUB_KEY" >> /root/.ssh/authorized_keys' not in template
    assert "Both SSH keys are required" not in template
    assert "LEGACY_APPROVER_PUB_KEY" not in template
    assert "remove_legacy_approver_key" not in template
    # The arg shift must be in place: dashboard URL is now the 2nd positional arg.
    assert 'DASHBOARD_URL="$2"' in template


def test_enroll_script_does_not_reference_approver():
    main = _read("dashboard/main.py")
    # The enrollment one-liner must no longer embed or pass an approver key.
    assert "APPROVER_KEY='" not in main
    assert '"$APPROVER_KEY"' not in main
    # Single-key save signature.
    assert "save_ssh_keys(payload.eshu_key)" in main


def test_schema_has_no_approver_field():
    schema = _read("dashboard/schemas.py")
    assert "approver_key" not in schema

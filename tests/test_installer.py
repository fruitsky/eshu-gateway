"""Regression tests for the gateway installer template.

The write_poller/write_gateway `__GATEWAY_TOKEN__` substitution must be scoped to
the header assignment ONLY. A global (`g`-flag) replace rewrites the poller's
self-heal placeholder check — `[ "$GATEWAY_TOKEN" = "__GATEWAY_TOKEN__" ]` — into
`[ = "<real-token>" ]` (always true), which made every gateway re-register with
the dashboard every poll cycle (the '/api/register' audit flood).
"""
import os
import re

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def _read(path):
    with open(os.path.join(ROOT, path), "r", encoding="utf-8") as f:
        return f.read()


def test_poller_self_heal_placeholder_survives_write_poller_sed():
    poller = _read("dashboard/eshu-poller.sh")
    # Apply the exact (fixed) write_poller sed: header assignment line only.
    fixed = re.sub(r'^GATEWAY_TOKEN="__GATEWAY_TOKEN__"',
                   'GATEWAY_TOKEN="tok123"', poller, flags=re.M)
    # Header replaced with the token...
    assert 'GATEWAY_TOKEN="tok123"' in fixed
    # ...and the self-heal condition's placeholder survives (fires only when empty).
    assert '[ "$GATEWAY_TOKEN" = "__GATEWAY_TOKEN__" ]' in fixed
    assert fixed.count("__GATEWAY_TOKEN__") == 1


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
